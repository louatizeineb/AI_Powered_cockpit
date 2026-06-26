from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.types import Command
from sqlalchemy.engine import Engine

from app.migration_v2.orchestration.checkpoints import postgres_checkpointer
from app.migration_v2.orchestration.approval_service import (
    SchemaApprovalCommand,
    pending_schema_mapping_keys,
)
from app.migration_v2.orchestration.repository import WorkflowRepository
from app.migration_v2.orchestration.schema_graph import SchemaGraphRuntime, build_schema_agent_graph
from app.migration_v2.orchestration.state import WorkflowPhase, WorkflowStatus


class PersistentSchemaOrchestrator:
    CHECKPOINT_NAMESPACE = "schema-agent-team-v1"

    def __init__(
        self,
        *,
        engine: Engine,
        postgres_url: str,
        env_config_path: str,
        contract_path: str,
        require_llm: bool = False,
        refresh_tools: bool = False,
    ):
        self.engine = engine
        self.postgres_url = postgres_url
        self.repository = WorkflowRepository(engine)
        self.runtime = SchemaGraphRuntime(
            engine=engine,
            repository=self.repository,
            postgres_url=postgres_url,
            env_config_path=str(Path(env_config_path).resolve()),
            contract_path=str(Path(contract_path).resolve()),
            require_llm=require_llm,
            refresh_tools=refresh_tools,
        )

    def config(
        self,
        thread_id: str,
        *,
        run_id: str | None = None,
        export_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": f"{self.CHECKPOINT_NAMESPACE}:{thread_id}",
            },
            "run_name": "migration-v2-schema-orchestrator",
            "tags": ["migration-v2", "schema-agent-team", self.CHECKPOINT_NAMESPACE],
            "metadata": {
                "migration_run_id": run_id,
                "export_id": export_id,
                "checkpoint_namespace": self.CHECKPOINT_NAMESPACE,
            },
        }

    def start(self, export_id: str, *, created_by: str = "persistent-orchestrator") -> dict[str, Any]:
        state, created = self.repository.create_or_get_run(
            export_id=export_id,
            workflow_version="1.0.0",
            trigger_type="manual",
            trigger_payload={"orchestrator": "langgraph", "namespace": self.CHECKPOINT_NAMESPACE},
            created_by=created_by,
        )
        state = self.repository.transition(
            state,
            to_status=WorkflowStatus.RUNNING,
            to_phase=WorkflowPhase.RECEIVED,
            actor_type="orchestrator",
            actor_name="PersistentSchemaOrchestrator",
            reason="Start or restart persistent LangGraph schema workflow.",
        )
        with self.repository.workflow_lock(state.run_id):
            with postgres_checkpointer(self.postgres_url, setup=True) as saver:
                graph = build_schema_agent_graph(self.runtime, saver)
                graph.invoke(
                    state.snapshot(),
                    config=self.config(
                        state.thread_id,
                        run_id=state.run_id,
                        export_id=state.export_id,
                    ),
                )
                response = self._snapshot_response(graph, state.run_id, state.thread_id)
        response["workflow_run_created"] = created
        return response

    def resume(self, run_id: str, command_payload: dict[str, Any]) -> dict[str, Any]:
        state = self.repository.get_run(run_id)
        command = SchemaApprovalCommand.model_validate(command_payload)
        if command.decision == "approve":
            pending = pending_schema_mapping_keys(self.engine, run_id)
            resolutions = {
                (item.raw_table_name, item.raw_column_name) for item in command.resolutions
            }
            if resolutions != pending:
                missing = sorted(pending - resolutions)
                extra = sorted(resolutions - pending)
                raise ValueError(
                    f"Approval resolutions do not match pending proposals. missing={missing} extra={extra}"
                )
        with self.repository.workflow_lock(state.run_id):
            with postgres_checkpointer(self.postgres_url, setup=True) as saver:
                graph = build_schema_agent_graph(self.runtime, saver)
                graph.invoke(
                    Command(resume=command.model_dump(mode="json")),
                    config=self.config(
                        state.thread_id,
                        run_id=state.run_id,
                        export_id=state.export_id,
                    ),
                )
                return self._snapshot_response(graph, state.run_id, state.thread_id)

    def status(self, run_id: str) -> dict[str, Any]:
        state = self.repository.get_run(run_id)
        with postgres_checkpointer(self.postgres_url, setup=True) as saver:
            graph = build_schema_agent_graph(self.runtime, saver)
            return self._snapshot_response(graph, state.run_id, state.thread_id)

    def _snapshot_response(self, graph, run_id: str, thread_id: str) -> dict[str, Any]:
        durable_state = self.repository.get_run(run_id)
        snapshot = graph.get_state(
            self.config(
                thread_id,
                run_id=run_id,
                export_id=durable_state.export_id,
            )
        )
        interrupts = [
            interrupt.value
            for task in snapshot.tasks
            for interrupt in getattr(task, "interrupts", ())
        ]
        return {
            "run_id": run_id,
            "thread_id": thread_id,
            "status": durable_state.status,
            "current_phase": durable_state.current_phase,
            "next_nodes": list(snapshot.next),
            "interrupts": interrupts,
            "state": durable_state.snapshot(),
        }
