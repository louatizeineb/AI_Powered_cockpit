from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.migration_v2.orchestration.state import MigrationRunState, WorkflowPhase, WorkflowStatus


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def compute_export_fingerprint(
    files: Iterable[Mapping[str, Any]],
    contract_version: str,
) -> str:
    file_evidence = sorted(
        (
            {
                "raw_table_name": str(item.get("raw_table_name") or ""),
                "file_path": str(item.get("file_path") or ""),
                "file_hash": str(item.get("file_hash") or ""),
            }
            for item in files
        ),
        key=lambda item: (item["raw_table_name"], item["file_path"], item["file_hash"]),
    )
    return stable_hash({"contract_version": contract_version, "files": file_evidence})


def workflow_idempotency_key(
    export_id: str,
    export_fingerprint: str,
    contract_version: str,
    workflow_version: str,
) -> str:
    return stable_hash(
        {
            "export_id": export_id,
            "export_fingerprint": export_fingerprint,
            "contract_version": contract_version,
            "workflow_version": workflow_version,
        }
    )


class WorkflowRepository:
    """Durable control-plane repository; it never writes catalog or graph data."""

    def __init__(self, engine: Engine):
        self.engine = engine

    @contextmanager
    def workflow_lock(self, run_id: str):
        """Prevent concurrent advancement of one durable workflow thread."""

        with self.engine.connect() as conn:
            acquired = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(hashtext(:run_id))"),
                    {"run_id": run_id},
                ).scalar_one()
            )
            if not acquired:
                raise RuntimeError(f"Workflow {run_id} is already being advanced by another worker.")
            try:
                yield
            finally:
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:run_id))"),
                    {"run_id": run_id},
                )

    def registered_export_evidence(self, export_id: str) -> dict[str, Any]:
        with self.engine.connect() as conn:
            export = conn.execute(
                text(
                    """
                    SELECT export_id, export_path, contract_version
                    FROM migration_export_run
                    WHERE export_id = :export_id
                    """
                ),
                {"export_id": export_id},
            ).mappings().first()
            if export is None:
                raise ValueError(f"Export {export_id!r} is not registered.")
            files = conn.execute(
                text(
                    """
                    SELECT raw_table_name, file_path, file_hash
                    FROM migration_raw_file
                    WHERE export_id = :export_id
                    ORDER BY raw_table_name, file_path
                    """
                ),
                {"export_id": export_id},
            ).mappings().all()
        if not files:
            raise ValueError(f"Export {export_id!r} has no registered files.")
        return {"export": dict(export), "files": [dict(row) for row in files]}

    def create_or_get_run(
        self,
        *,
        export_id: str,
        workflow_version: str,
        trigger_type: str,
        trigger_payload: Mapping[str, Any] | None = None,
        created_by: str | None = None,
    ) -> tuple[MigrationRunState, bool]:
        evidence = self.registered_export_evidence(export_id)
        export = evidence["export"]
        contract_version = str(export.get("contract_version") or "unknown")
        fingerprint = compute_export_fingerprint(evidence["files"], contract_version)
        idempotency_key = workflow_idempotency_key(
            export_id,
            fingerprint,
            contract_version,
            workflow_version,
        )
        run_id = str(uuid4())
        thread_id = f"migration-v2:{run_id}"
        state = MigrationRunState(
            run_id=run_id,
            thread_id=thread_id,
            export_id=export_id,
            export_path=export.get("export_path"),
            export_fingerprint=fingerprint,
            contract_version=contract_version,
            workflow_version=workflow_version,
            trigger_type=trigger_type,
            discovered_files=evidence["files"],
        )
        params = {
            "run_id": run_id,
            "export_id": export_id,
            "workflow_version": workflow_version,
            "contract_version": contract_version,
            "export_fingerprint": fingerprint,
            "idempotency_key": idempotency_key,
            "thread_id": thread_id,
            "trigger_type": trigger_type,
            "trigger_payload": canonical_json(dict(trigger_payload or {})),
            "state": canonical_json(state.snapshot()),
            "created_by": created_by,
        }
        with self.engine.begin() as conn:
            inserted = conn.execute(
                text(
                    """
                    INSERT INTO migration_workflow_run(
                        run_id, export_id, workflow_version, contract_version,
                        export_fingerprint, idempotency_key, thread_id, trigger_type,
                        trigger_payload, status, current_phase, state, created_by
                    )
                    VALUES (
                        CAST(:run_id AS uuid), :export_id, :workflow_version, :contract_version,
                        :export_fingerprint, :idempotency_key, :thread_id, :trigger_type,
                        CAST(:trigger_payload AS jsonb), 'received', 'received',
                        CAST(:state AS jsonb), :created_by
                    )
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING run_id
                    """
                ),
                params,
            ).scalar()
            created = inserted is not None
            row = conn.execute(
                text(
                    """
                    SELECT state
                    FROM migration_workflow_run
                    WHERE idempotency_key = :idempotency_key
                    """
                ),
                {"idempotency_key": idempotency_key},
            ).scalar_one()
        return MigrationRunState.model_validate(row), created

    def get_run(self, run_id: str) -> MigrationRunState:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT state FROM migration_workflow_run WHERE run_id = CAST(:run_id AS uuid)"),
                {"run_id": run_id},
            ).scalar()
        if row is None:
            raise KeyError(f"Workflow run not found: {run_id}")
        return MigrationRunState.model_validate(row)

    def transition(
        self,
        state: MigrationRunState,
        *,
        to_status: WorkflowStatus,
        to_phase: WorkflowPhase,
        actor_type: str,
        actor_name: str,
        reason: str = "",
    ) -> MigrationRunState:
        previous_status = str(state.status)
        previous_phase = str(state.current_phase)
        updated = state.model_copy(
            update={
                "status": to_status,
                "current_phase": to_phase,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        snapshot = updated.snapshot()
        with self.engine.begin() as conn:
            locked = conn.execute(
                text(
                    """
                    SELECT run_id FROM migration_workflow_run
                    WHERE run_id = CAST(:run_id AS uuid)
                    FOR UPDATE
                    """
                ),
                {"run_id": state.run_id},
            ).scalar()
            if locked is None:
                raise KeyError(f"Workflow run not found: {state.run_id}")
            conn.execute(
                text(
                    """
                    INSERT INTO migration_workflow_transition(
                        run_id, from_status, to_status, from_phase, to_phase,
                        actor_type, actor_name, reason, state_snapshot
                    )
                    VALUES (
                        CAST(:run_id AS uuid), :from_status, :to_status, :from_phase, :to_phase,
                        :actor_type, :actor_name, :reason, CAST(:state_snapshot AS jsonb)
                    )
                    """
                ),
                {
                    "run_id": state.run_id,
                    "from_status": previous_status,
                    "to_status": str(to_status),
                    "from_phase": previous_phase,
                    "to_phase": str(to_phase),
                    "actor_type": actor_type,
                    "actor_name": actor_name,
                    "reason": reason,
                    "state_snapshot": canonical_json(snapshot),
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE migration_workflow_run
                    SET status = :status,
                        current_phase = :phase,
                        state = CAST(:state AS jsonb),
                        started_at = CASE WHEN started_at IS NULL AND :status = 'running' THEN now() ELSE started_at END,
                        completed_at = CASE WHEN :status IN ('published', 'failed', 'cancelled') THEN now() ELSE completed_at END,
                        updated_at = now()
                    WHERE run_id = CAST(:run_id AS uuid)
                    """
                ),
                {
                    "run_id": state.run_id,
                    "status": str(to_status),
                    "phase": str(to_phase),
                    "state": canonical_json(snapshot),
                },
            )
        return updated

    def save_checkpoint(
        self,
        state: MigrationRunState,
        checkpoint_id: str,
        *,
        namespace: str = "migration_v2",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO migration_workflow_checkpoint(
                        run_id, thread_id, checkpoint_namespace, checkpoint_id,
                        phase, state, metadata
                    )
                    VALUES (
                        CAST(:run_id AS uuid), :thread_id, :namespace, :checkpoint_id,
                        :phase, CAST(:state AS jsonb), CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (thread_id, checkpoint_namespace, checkpoint_id) DO UPDATE
                    SET phase = EXCLUDED.phase,
                        state = EXCLUDED.state,
                        metadata = EXCLUDED.metadata,
                        created_at = now()
                    """
                ),
                {
                    "run_id": state.run_id,
                    "thread_id": state.thread_id,
                    "namespace": namespace,
                    "checkpoint_id": checkpoint_id,
                    "phase": str(state.current_phase),
                    "state": canonical_json(state.snapshot()),
                    "metadata": canonical_json(dict(metadata or {})),
                },
            )

    def request_approval(
        self,
        state: MigrationRunState,
        *,
        gate_name: str,
        requested_by: str,
        question: str,
        evidence: Mapping[str, Any] | None = None,
        required_role: str | None = None,
    ) -> str:
        approval_id = str(uuid4())
        with self.engine.begin() as conn:
            existing = conn.execute(
                text(
                    """
                    SELECT approval_id FROM migration_approval_request
                    WHERE run_id = CAST(:run_id AS uuid)
                      AND gate_name = :gate_name
                      AND status = 'pending'
                    """
                ),
                {"run_id": state.run_id, "gate_name": gate_name},
            ).scalar()
            if existing:
                return str(existing)
            conn.execute(
                text(
                    """
                    INSERT INTO migration_approval_request(
                        approval_id, run_id, gate_name, requested_by, required_role,
                        question, evidence
                    )
                    VALUES (
                        CAST(:approval_id AS uuid), CAST(:run_id AS uuid), :gate_name,
                        :requested_by, :required_role, :question, CAST(:evidence AS jsonb)
                    )
                    """
                ),
                {
                    "approval_id": approval_id,
                    "run_id": state.run_id,
                    "gate_name": gate_name,
                    "requested_by": requested_by,
                    "required_role": required_role,
                    "question": question,
                    "evidence": canonical_json(dict(evidence or {})),
                },
            )
        return approval_id

    def resolve_approval(
        self,
        approval_id: str,
        *,
        decision: str,
        rationale: str,
        decided_by: str,
    ) -> None:
        normalized = decision.strip().lower()
        if normalized not in {"approved", "rejected", "cancelled"}:
            raise ValueError("decision must be approved, rejected, or cancelled")
        with self.engine.begin() as conn:
            updated = conn.execute(
                text(
                    """
                    UPDATE migration_approval_request
                    SET status = :status,
                        decision = :decision,
                        rationale = :rationale,
                        decided_by = :decided_by,
                        decided_at = now()
                    WHERE approval_id = CAST(:approval_id AS uuid)
                      AND status = 'pending'
                    """
                ),
                {
                    "approval_id": approval_id,
                    "status": normalized,
                    "decision": normalized,
                    "rationale": rationale,
                    "decided_by": decided_by,
                },
            )
        if updated.rowcount != 1:
            raise ValueError(f"Pending approval not found: {approval_id}")

    def start_tool_execution(
        self,
        state: MigrationRunState,
        *,
        tool_name: str,
        tool_version: str,
        input_payload: Mapping[str, Any],
        agent_name: str | None = None,
    ) -> tuple[str, bool]:
        input_hash = stable_hash(input_payload)
        key = stable_hash(
            {
                "tool_name": tool_name,
                "tool_version": tool_version,
                "input_hash": input_hash,
                "phase": state.current_phase,
            }
        )
        execution_id = str(uuid4())
        with self.engine.begin() as conn:
            inserted = conn.execute(
                text(
                    """
                    INSERT INTO migration_tool_execution(
                        execution_id, run_id, tool_name, tool_version, agent_name,
                        idempotency_key, status, input_hash, input_payload, started_at
                    )
                    VALUES (
                        CAST(:execution_id AS uuid), CAST(:run_id AS uuid), :tool_name,
                        :tool_version, :agent_name, :idempotency_key, 'running',
                        :input_hash, CAST(:input_payload AS jsonb), now()
                    )
                    ON CONFLICT (run_id, idempotency_key) DO NOTHING
                    RETURNING execution_id
                    """
                ),
                {
                    "execution_id": execution_id,
                    "run_id": state.run_id,
                    "tool_name": tool_name,
                    "tool_version": tool_version,
                    "agent_name": agent_name,
                    "idempotency_key": key,
                    "input_hash": input_hash,
                    "input_payload": canonical_json(dict(input_payload)),
                },
            ).scalar()
            if inserted is not None:
                return str(inserted), True
            existing = conn.execute(
                text(
                    """
                    SELECT execution_id FROM migration_tool_execution
                    WHERE run_id = CAST(:run_id AS uuid) AND idempotency_key = :idempotency_key
                    """
                ),
                {"run_id": state.run_id, "idempotency_key": key},
            ).scalar_one()
        return str(existing), False

    def finish_tool_execution(
        self,
        execution_id: str,
        *,
        status: str,
        output_payload: Mapping[str, Any] | None = None,
        generated_artifacts: Iterable[str] = (),
        database_effects: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        normalized = status.strip().lower()
        if normalized not in {"completed", "failed", "skipped"}:
            raise ValueError("terminal tool status must be completed, failed, or skipped")
        with self.engine.begin() as conn:
            updated = conn.execute(
                text(
                    """
                    UPDATE migration_tool_execution
                    SET status = :status,
                        output_payload = CAST(:output_payload AS jsonb),
                        generated_artifacts = CAST(:generated_artifacts AS jsonb),
                        database_effects = CAST(:database_effects AS jsonb),
                        error = CAST(:error AS jsonb),
                        completed_at = now()
                    WHERE execution_id = CAST(:execution_id AS uuid)
                      AND status IN ('pending', 'running')
                    """
                ),
                {
                    "execution_id": execution_id,
                    "status": normalized,
                    "output_payload": canonical_json(dict(output_payload or {})),
                    "generated_artifacts": canonical_json(list(generated_artifacts)),
                    "database_effects": canonical_json(dict(database_effects or {})),
                    "error": canonical_json(dict(error)) if error else None,
                },
            )
        if updated.rowcount != 1:
            raise ValueError(f"Active tool execution not found: {execution_id}")

    def get_tool_execution(self, execution_id: str) -> dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT execution_id::text, tool_name, tool_version, status,
                           input_hash, output_payload, generated_artifacts, database_effects, error,
                           started_at, completed_at
                    FROM migration_tool_execution
                    WHERE execution_id = CAST(:execution_id AS uuid)
                    """
                ),
                {"execution_id": execution_id},
            ).mappings().first()
        if row is None:
            raise KeyError(f"Tool execution not found: {execution_id}")
        return dict(row)
