from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.migration_v2.orchestration.repository import WorkflowRepository


class SchemaMappingResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_table_name: str
    raw_column_name: str
    action: Literal["keep_contract_missing"]


class SchemaApprovalCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    decided_by: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    resolutions: list[SchemaMappingResolution] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_resolutions_for_approval(self):
        if self.decision == "approve" and not self.resolutions:
            raise ValueError("Approval requires one resolution for every pending mapping proposal.")
        return self


def pending_schema_mapping_keys(engine: Engine, workflow_run_id: str) -> set[tuple[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT raw_table_name, raw_column_name
                FROM migration_schema_mapping_proposal
                WHERE workflow_run_id = CAST(:workflow_run_id AS uuid)
                  AND status = 'pending'
                """
            ),
            {"workflow_run_id": workflow_run_id},
        ).all()
    return {(str(row[0]), str(row[1])) for row in rows}


def apply_schema_mapping_decision(
    engine: Engine,
    workflow_repository: WorkflowRepository,
    *,
    workflow_run_id: str,
    approval_id: str,
    command_payload: dict[str, Any],
) -> dict[str, Any]:
    command = SchemaApprovalCommand.model_validate(command_payload)
    pending = pending_schema_mapping_keys(engine, workflow_run_id)
    if not pending:
        raise ValueError("No pending schema mapping proposals exist for this workflow.")

    if command.decision == "reject":
        with engine.begin() as conn:
            updated = conn.execute(
                text(
                    """
                    UPDATE migration_schema_mapping_proposal
                    SET status = 'rejected',
                        reviewer_action = 'reject',
                        reviewer_rationale = :rationale,
                        reviewed_by = :decided_by,
                        reviewed_at = now()
                    WHERE workflow_run_id = CAST(:workflow_run_id AS uuid)
                      AND status = 'pending'
                    """
                ),
                {
                    "workflow_run_id": workflow_run_id,
                    "rationale": command.rationale,
                    "decided_by": command.decided_by,
                },
            ).rowcount
        workflow_repository.resolve_approval(
            approval_id,
            decision="rejected",
            rationale=command.rationale,
            decided_by=command.decided_by,
        )
        return {"decision": "rejected", "proposal_count": int(updated or 0)}

    resolutions = {
        (item.raw_table_name, item.raw_column_name): item for item in command.resolutions
    }
    if set(resolutions) != pending:
        missing = sorted(pending - set(resolutions))
        extra = sorted(set(resolutions) - pending)
        raise ValueError(f"Approval resolutions do not match pending proposals. missing={missing} extra={extra}")

    with engine.begin() as conn:
        for key, resolution in resolutions.items():
            table_name, column_name = key
            conn.execute(
                text(
                    """
                    UPDATE migration_schema_mapping_proposal
                    SET status = 'approved',
                        reviewer_action = :reviewer_action,
                        reviewer_rationale = :rationale,
                        reviewed_by = :decided_by,
                        reviewed_at = now(),
                        approved_by = :decided_by,
                        approved_at = now()
                    WHERE workflow_run_id = CAST(:workflow_run_id AS uuid)
                      AND raw_table_name = :raw_table_name
                      AND raw_column_name = :raw_column_name
                      AND status = 'pending'
                    """
                ),
                {
                    "workflow_run_id": workflow_run_id,
                    "raw_table_name": table_name,
                    "raw_column_name": column_name,
                    "reviewer_action": resolution.action,
                    "rationale": command.rationale,
                    "decided_by": command.decided_by,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE migration_mapping_decision
                    SET decision_type = 'accepted_missing_optional',
                        requires_human_approval = false,
                        approved_by = :decided_by,
                        approved_at = now(),
                        rationale = :rationale,
                        evidence = coalesce(evidence, '{}'::jsonb) || CAST(:decision_evidence AS jsonb)
                    WHERE export_id = (
                        SELECT export_id FROM migration_workflow_run
                        WHERE run_id = CAST(:workflow_run_id AS uuid)
                    )
                      AND raw_table_name = :raw_table_name
                      AND raw_column_name = :raw_column_name
                    """
                ),
                {
                    "workflow_run_id": workflow_run_id,
                    "raw_table_name": table_name,
                    "raw_column_name": column_name,
                    "decided_by": command.decided_by,
                    "rationale": command.rationale,
                    "decision_evidence": json.dumps(
                        {
                            "reviewer_action": resolution.action,
                            "approval_id": approval_id,
                            "workflow_run_id": workflow_run_id,
                        }
                    ),
                },
            )

    workflow_repository.resolve_approval(
        approval_id,
        decision="approved",
        rationale=command.rationale,
        decided_by=command.decided_by,
    )
    return {
        "decision": "approved",
        "proposal_count": len(resolutions),
        "actions": {"keep_contract_missing": len(resolutions)},
    }
