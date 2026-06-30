from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.migration_v2.agents.execution import AgentContext
from app.migration_v2.agents.export_detection_agent import run as run_export_intake
from app.migration_v2.agents.mapping_agent import run as run_mapping_ontology
from app.migration_v2.agents.schema_profiling_agent import run as run_schema_intelligence
from app.migration_v2.orchestration.approval_service import apply_schema_mapping_decision
from app.migration_v2.orchestration.repository import WorkflowRepository
from app.migration_v2.orchestration.state import MigrationGraphState, MigrationRunState, WorkflowPhase, WorkflowStatus
from app.migration_v2.orchestration.tool_runtime import AllowlistedToolRuntime


@dataclass(frozen=True)
class SchemaGraphRuntime:
    engine: Any
    repository: WorkflowRepository
    postgres_url: str
    env_config_path: str
    contract_path: str
    require_llm: bool = False
    refresh_tools: bool = False

    def agent_context(self, state: MigrationRunState) -> AgentContext:
        return AgentContext(
            engine=self.engine,
            workflow_repository=self.repository,
            tool_runtime=AllowlistedToolRuntime(self.repository, state, self.postgres_url),
            state=state,
            contract_path=self.contract_path,
            env_config_path=self.env_config_path,
            require_llm=self.require_llm,
            refresh_tools=self.refresh_tools,
        )


def result_payload(result) -> dict[str, Any]:
    payload = asdict(result)
    # Raw model output remains persisted in proposal tables; workflow state keeps the decision evidence concise.
    for proposal in payload.get("proposals") or []:
        proposal.pop("raw_model_response", None)
    return payload


def build_schema_agent_graph(runtime: SchemaGraphRuntime, checkpointer: object | None = None):
    repository = runtime.repository

    def export_intake_node(graph_state: MigrationGraphState) -> dict[str, Any]:
        state = MigrationRunState.model_validate(graph_state)
        result = run_export_intake(runtime.agent_context(state))
        state = state.model_copy(
            update={"agent_results": {**state.agent_results, result.agent_name: result_payload(result)}}
        )
        if result.status == "blocked":
            state = repository.transition(
                state,
                to_status=WorkflowStatus.BLOCKED,
                to_phase=WorkflowPhase.RECEIVED,
                actor_type="agent",
                actor_name=result.agent_name,
                reason="; ".join(result.errors),
            )
        else:
            state = repository.transition(
                state,
                to_status=WorkflowStatus.RUNNING,
                to_phase=WorkflowPhase.REGISTERED,
                actor_type="agent",
                actor_name=result.agent_name,
                reason="Registered export evidence verified.",
            )
        repository.save_checkpoint(state, "langgraph-export-intake", metadata={"status": result.status})
        return state.snapshot()

    def route_after_export(graph_state: MigrationGraphState) -> Literal["schema_intelligence", "end"]:
        return "end" if graph_state.get("status") == WorkflowStatus.BLOCKED else "schema_intelligence"

    def schema_intelligence_node(graph_state: MigrationGraphState) -> dict[str, Any]:
        state = MigrationRunState.model_validate(graph_state)
        result = run_schema_intelligence(runtime.agent_context(state))
        state = state.model_copy(
            update={"agent_results": {**state.agent_results, result.agent_name: result_payload(result)}}
        )
        if result.status != "completed":
            state = repository.transition(
                state,
                to_status=WorkflowStatus.BLOCKED,
                to_phase=WorkflowPhase.REGISTERED,
                actor_type="agent",
                actor_name=result.agent_name,
                reason="; ".join(result.errors) or "Schema Intelligence KG audit failed.",
            )
        else:
            state = repository.transition(
                state,
                to_status=WorkflowStatus.RUNNING,
                to_phase=WorkflowPhase.PROFILED,
                actor_type="agent",
                actor_name=result.agent_name,
                reason="Profiles projected into the Schema Intelligence KG.",
            )
        repository.save_checkpoint(state, "langgraph-schema-intelligence", metadata={"status": result.status})
        return state.snapshot()

    def route_after_schema(graph_state: MigrationGraphState) -> Literal["mapping_ontology", "end"]:
        return "end" if graph_state.get("status") == WorkflowStatus.BLOCKED else "mapping_ontology"

    def mapping_ontology_node(graph_state: MigrationGraphState) -> dict[str, Any]:
        state = MigrationRunState.model_validate(graph_state)
        result = run_mapping_ontology(runtime.agent_context(state))
        proposals = [asdict(proposal) for proposal in result.proposals]
        for proposal in proposals:
            proposal.pop("raw_model_response", None)
        state = state.model_copy(
            update={
                "agent_results": {**state.agent_results, result.agent_name: result_payload(result)},
                "agent_proposals": proposals,
            }
        )
        if result.errors and not proposals:
            state = repository.transition(
                state,
                to_status=WorkflowStatus.BLOCKED,
                to_phase=WorkflowPhase.DRIFT_REVIEW,
                actor_type="agent",
                actor_name=result.agent_name,
                reason="; ".join(result.errors),
            )
        elif proposals:
            approval_id = repository.request_approval(
                state,
                gate_name="schema_mapping_review",
                requested_by=result.agent_name,
                required_role="data_steward",
                question=f"Resolve {len(proposals)} schema mapping proposals.",
                evidence={"proposal_count": len(proposals), "export_id": state.export_id},
            )
            pending = {
                "approval_id": approval_id,
                "gate_name": "schema_mapping_review",
                "proposal_count": len(proposals),
                "allowed_actions": ["keep_contract_missing", "reject"],
            }
            requirements = [
                pending,
                *[item for item in state.approval_requirements if item.get("gate_name") != "schema_mapping_review"],
            ]
            state = state.model_copy(
                update={"pending_approval": pending, "approval_requirements": requirements}
            )
            state = repository.transition(
                state,
                to_status=WorkflowStatus.WAITING_APPROVAL,
                to_phase=WorkflowPhase.DRIFT_REVIEW,
                actor_type="agent",
                actor_name=result.agent_name,
                reason=f"{len(proposals)} schema mappings require a steward decision.",
            )
        else:
            state = repository.transition(
                state,
                to_status=WorkflowStatus.RUNNING,
                to_phase=WorkflowPhase.MAPPED,
                actor_type="agent",
                actor_name=result.agent_name,
                reason="No unresolved schema mappings remain.",
            )
        repository.save_checkpoint(state, "langgraph-mapping-ontology", metadata={"status": result.status})
        return state.snapshot()

    def route_after_mapping(graph_state: MigrationGraphState) -> Literal["approval_interrupt", "finalize_mapping", "end"]:
        status = graph_state.get("status")
        if status == WorkflowStatus.WAITING_APPROVAL:
            return "approval_interrupt"
        if status == WorkflowStatus.BLOCKED:
            return "end"
        return "finalize_mapping"

    def approval_interrupt_node(graph_state: MigrationGraphState) -> dict[str, Any]:
        state = MigrationRunState.model_validate(graph_state)
        pending = state.pending_approval or {}
        command_payload = interrupt(
            {
                "type": "schema_mapping_review",
                "run_id": state.run_id,
                "approval": pending,
                "proposals": state.agent_proposals,
                "required_resolution_shape": {
                    "decision": "approve | reject",
                    "decided_by": "reviewer identifier",
                    "rationale": "decision rationale",
                    "resolutions": [
                        {
                            "raw_table_name": "...",
                            "raw_column_name": "...",
                            "action": "keep_contract_missing",
                        }
                    ],
                },
            }
        )
        outcome = apply_schema_mapping_decision(
            runtime.engine,
            repository,
            workflow_run_id=state.run_id,
            approval_id=str(pending["approval_id"]),
            command_payload=command_payload,
        )
        decisions = [*state.approval_decisions, outcome]
        if outcome["decision"] == "rejected":
            state = state.model_copy(update={"pending_approval": None, "approval_decisions": decisions})
            state = repository.transition(
                state,
                to_status=WorkflowStatus.BLOCKED,
                to_phase=WorkflowPhase.DRIFT_REVIEW,
                actor_type="human",
                actor_name=str(command_payload["decided_by"]),
                reason=str(command_payload["rationale"]),
            )
        else:
            state = state.model_copy(
                update={
                    "pending_approval": None,
                    "approval_decisions": decisions,
                    "approval_requirements": [
                        item
                        for item in state.approval_requirements
                        if item.get("gate_name") != "schema_mapping_review"
                    ],
                }
            )
            state = repository.transition(
                state,
                to_status=WorkflowStatus.RUNNING,
                to_phase=WorkflowPhase.DRIFT_REVIEW,
                actor_type="human",
                actor_name=str(command_payload["decided_by"]),
                reason=str(command_payload["rationale"]),
            )
        repository.save_checkpoint(state, "langgraph-schema-approval", metadata=outcome)
        return state.snapshot()

    def route_after_approval(graph_state: MigrationGraphState) -> Literal["finalize_mapping", "end"]:
        return "end" if graph_state.get("status") == WorkflowStatus.BLOCKED else "finalize_mapping"

    def finalize_mapping_node(graph_state: MigrationGraphState) -> dict[str, Any]:
        state = MigrationRunState.model_validate(graph_state)
        mapping_tools = AllowlistedToolRuntime(repository, state, runtime.postgres_url)
        mapping_tools.execute(
            agent_name="MappingOntologyAgent",
            tool_name="generate_mapping_plan",
            payload={"export_id": state.export_id},
        )
        schema_tools = AllowlistedToolRuntime(repository, state, runtime.postgres_url)
        schema_tools.execute(
            agent_name="SchemaIntelligenceAgent",
            tool_name="build_schema_intelligence_kg",
            payload={
                "export_id": state.export_id,
                "env_config": runtime.env_config_path,
                "contract": runtime.contract_path,
            },
        )
        state = repository.transition(
            state,
            to_status=WorkflowStatus.RUNNING,
            to_phase=WorkflowPhase.MAPPED,
            actor_type="orchestrator",
            actor_name="PersistentSchemaOrchestrator",
            reason="Approved schema decisions applied; mapping plan and Schema KG refreshed.",
        )
        repository.save_checkpoint(state, "langgraph-mapped", metadata={"status": "completed"})
        return state.snapshot()

    def conditional_governance_node(graph_state: MigrationGraphState) -> dict[str, Any]:
        state = MigrationRunState.model_validate(graph_state)
        tools = AllowlistedToolRuntime(repository, state, runtime.postgres_url)
        try:
            tools.execute(
                agent_name="PreprocessingAgent",
                tool_name="preprocess_staging",
                payload={"export_id": state.export_id, "contract": runtime.contract_path},
                refresh=runtime.refresh_tools,
            )
            state = repository.transition(
                state,
                to_status=WorkflowStatus.RUNNING,
                to_phase=WorkflowPhase.STAGED,
                actor_type="agent",
                actor_name="PreprocessingAgent",
                reason="Canonical staging rebuilt through the allowlisted runtime.",
            )
            tools.state = state
            tools.execute(
                agent_name="ValidationAgent",
                tool_name="validate_staging",
                payload={"export_id": state.export_id, "contract": runtime.contract_path},
                refresh=runtime.refresh_tools,
            )
            state = repository.transition(
                state,
                to_status=WorkflowStatus.RUNNING,
                to_phase=WorkflowPhase.VALIDATED,
                actor_type="agent",
                actor_name="ValidationAgent",
                reason="Deterministic staging validation completed.",
            )
            tools.state = state
            tools.execute(
                agent_name="ValidationAgent",
                tool_name="populate_validation_queue",
                payload={"export_id": state.export_id, "env_config": runtime.env_config_path},
                refresh=runtime.refresh_tools,
            )
            projection = tools.execute(
                agent_name="ValidationAgent",
                tool_name="build_conditional_projection",
                payload={"export_id": state.export_id, "env_config": runtime.env_config_path},
                refresh=runtime.refresh_tools,
            )
            report_path = (
                Path(__file__).resolve().parents[4]
                / "reports" / "migration_v2" / state.export_id / "conditional_publish_report.json"
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            state = state.model_copy(
                update={
                    "publication_counts": {
                        **{f"object_{key}": value for key, value in report.get("object_counts", {}).items()},
                        **{f"relationship_{key}": value for key, value in report.get("relationship_counts", {}).items()},
                        "review_pending": int(report.get("review_pending_count") or 0),
                        "hard_blockers": len(report.get("hard_blockers") or []),
                    },
                    "generated_artifacts": [
                        *state.generated_artifacts,
                        str(report_path),
                    ],
                }
            )
            blocked = report.get("status") == "blocked"
            state = repository.transition(
                state,
                to_status=WorkflowStatus.BLOCKED if blocked else WorkflowStatus.RUNNING,
                to_phase=WorkflowPhase.QUEUE_REVIEW if blocked else WorkflowPhase.VALIDATED,
                actor_type="orchestrator",
                actor_name="PersistentSchemaOrchestrator",
                reason=(
                    f"Conditional projection has {len(report.get('hard_blockers') or [])} hard blocker(s)."
                    if blocked else "Trusted and quarantine projections are ready for candidate build."
                ),
            )
            repository.save_checkpoint(
                state,
                "langgraph-conditional-governance",
                metadata={"status": report.get("status"), "tool_execution": projection.get("execution_id")},
            )
        except Exception as exc:  # noqa: BLE001
            state = state.model_copy(update={"errors": [*state.errors, {"phase": "conditional_governance", "message": str(exc)}]})
            state = repository.transition(
                state,
                to_status=WorkflowStatus.FAILED,
                to_phase=WorkflowPhase.STAGED,
                actor_type="orchestrator",
                actor_name="PersistentSchemaOrchestrator",
                reason=str(exc),
            )
            repository.save_checkpoint(state, "langgraph-conditional-governance-failed", metadata={"error": str(exc)})
        return state.snapshot()

    builder = StateGraph(MigrationGraphState)
    builder.add_node("export_intake", export_intake_node)
    builder.add_node("schema_intelligence", schema_intelligence_node)
    builder.add_node("mapping_ontology", mapping_ontology_node)
    builder.add_node("approval_interrupt", approval_interrupt_node)
    builder.add_node("finalize_mapping", finalize_mapping_node)
    builder.add_node("conditional_governance", conditional_governance_node)
    builder.add_edge(START, "export_intake")
    builder.add_conditional_edges("export_intake", route_after_export, {"schema_intelligence": "schema_intelligence", "end": END})
    builder.add_conditional_edges("schema_intelligence", route_after_schema, {"mapping_ontology": "mapping_ontology", "end": END})
    builder.add_conditional_edges(
        "mapping_ontology",
        route_after_mapping,
        {"approval_interrupt": "approval_interrupt", "finalize_mapping": "finalize_mapping", "end": END},
    )
    builder.add_conditional_edges(
        "approval_interrupt",
        route_after_approval,
        {"finalize_mapping": "finalize_mapping", "end": END},
    )
    builder.add_edge("finalize_mapping", "conditional_governance")
    builder.add_edge("conditional_governance", END)
    return builder.compile(checkpointer=checkpointer)
