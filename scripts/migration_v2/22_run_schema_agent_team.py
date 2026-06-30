from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from _common import (
    DEFAULT_CONTRACT,
    ROOT,
    config_section,
    load_env_config,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)
from app.migration_v2.agents.execution import AgentContext
from app.migration_v2.agents.export_detection_agent import run as run_export_intake
from app.migration_v2.agents.mapping_agent import run as run_mapping_ontology
from app.migration_v2.agents.schema_profiling_agent import run as run_schema_intelligence
from app.migration_v2.orchestration.repository import WorkflowRepository
from app.migration_v2.orchestration.state import WorkflowPhase, WorkflowStatus
from app.migration_v2.orchestration.tool_runtime import AllowlistedToolRuntime


LOGGER = setup_logging("migration_v2.schema_agent_team")
AGENT_SQL = ROOT / "backend" / "migrations" / "sql" / "013_migration_v2_agent_runs.sql"
SCHEMA_AGENT_SQL = ROOT / "backend" / "migrations" / "sql" / "015_migration_v2_schema_agents.sql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the executable schema-intelligence agent team.")
    parser.add_argument("--export-id", required=True)
    parser.add_argument(
        "--env-config",
        default=str(ROOT / "configs" / "migration_v2" / "local_env.yaml"),
    )
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--created-by", default="schema-agent-team")
    parser.add_argument("--require-llm", action="store_true")
    parser.add_argument("--refresh-tools", action="store_true")
    return parser.parse_args()


def apply_sql(engine, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    with engine.begin() as conn:
        cursor = conn.connection.cursor()
        try:
            cursor.execute(sql)
        finally:
            cursor.close()


def context_for(args, engine, repository, state, postgres_url: str) -> AgentContext:
    return AgentContext(
        engine=engine,
        workflow_repository=repository,
        tool_runtime=AllowlistedToolRuntime(repository, state, postgres_url),
        state=state,
        contract_path=str(Path(args.contract).resolve()),
        env_config_path=str(Path(args.env_config).resolve()),
        require_llm=args.require_llm,
        refresh_tools=args.refresh_tools,
    )


def main() -> None:
    args = parse_args()
    config = load_env_config(args.env_config)
    postgres_url = str(config_section(config, "v2")["postgres_url"])
    engine = postgres_engine_from_url(postgres_url)
    apply_sql(engine, AGENT_SQL)
    apply_sql(engine, SCHEMA_AGENT_SQL)
    repository = WorkflowRepository(engine)
    state, created = repository.create_or_get_run(
        export_id=args.export_id,
        workflow_version="1.0.0",
        trigger_type="manual",
        trigger_payload={"command": "22_run_schema_agent_team.py"},
        created_by=args.created_by,
    )
    state = repository.transition(
        state,
        to_status=WorkflowStatus.RUNNING,
        to_phase=WorkflowPhase.RECEIVED,
        actor_type="orchestrator",
        actor_name="SchemaAgentTeam",
        reason="Start executable schema agent team.",
    )

    results = []
    intake = run_export_intake(context_for(args, engine, repository, state, postgres_url))
    results.append(intake)
    repository.save_checkpoint(state, "export-intake", metadata={"status": intake.status})
    if intake.status == "blocked":
        state = repository.transition(
            state,
            to_status=WorkflowStatus.BLOCKED,
            to_phase=WorkflowPhase.RECEIVED,
            actor_type="agent",
            actor_name=intake.agent_name,
            reason="; ".join(intake.errors),
        )
    else:
        state = repository.transition(
            state,
            to_status=WorkflowStatus.RUNNING,
            to_phase=WorkflowPhase.REGISTERED,
            actor_type="agent",
            actor_name=intake.agent_name,
            reason="Registered export evidence verified.",
        )
        schema = run_schema_intelligence(context_for(args, engine, repository, state, postgres_url))
        results.append(schema)
        repository.save_checkpoint(state, "schema-intelligence", metadata={"status": schema.status})
        if schema.status not in {"completed"}:
            state = repository.transition(
                state,
                to_status=WorkflowStatus.BLOCKED,
                to_phase=WorkflowPhase.REGISTERED,
                actor_type="agent",
                actor_name=schema.agent_name,
                reason="; ".join(schema.errors) or "Schema Intelligence KG audit failed.",
            )
        else:
            state = repository.transition(
                state,
                to_status=WorkflowStatus.RUNNING,
                to_phase=WorkflowPhase.PROFILED,
                actor_type="agent",
                actor_name=schema.agent_name,
                reason="Profiles projected into the Schema Intelligence KG.",
            )
            mapping = run_mapping_ontology(context_for(args, engine, repository, state, postgres_url))
            results.append(mapping)
            if mapping.proposals:
                requirement = {
                    "gate_name": "schema_mapping_review",
                    "proposal_count": len(mapping.proposals),
                    "agent_name": mapping.agent_name,
                }
                state = state.model_copy(
                    update={
                        "agent_proposals": [asdict(proposal) for proposal in mapping.proposals],
                        "approval_requirements": [
                            requirement,
                            *[
                                item
                                for item in state.approval_requirements
                                if item.get("gate_name") != "schema_mapping_review"
                            ],
                        ],
                    }
                )
                approval_id = repository.request_approval(
                    state,
                    gate_name="schema_mapping_review",
                    requested_by=mapping.agent_name,
                    required_role="data_steward",
                    question=f"Review {len(mapping.proposals)} unresolved schema mapping proposals.",
                    evidence={"proposal_count": len(mapping.proposals), "export_id": args.export_id},
                )
                requirement["approval_id"] = approval_id
                state = repository.transition(
                    state,
                    to_status=WorkflowStatus.WAITING_APPROVAL,
                    to_phase=WorkflowPhase.DRIFT_REVIEW,
                    actor_type="agent",
                    actor_name=mapping.agent_name,
                    reason=f"{len(mapping.proposals)} mapping proposals require approval.",
                )
            elif mapping.errors:
                state = repository.transition(
                    state,
                    to_status=WorkflowStatus.BLOCKED,
                    to_phase=WorkflowPhase.DRIFT_REVIEW,
                    actor_type="agent",
                    actor_name=mapping.agent_name,
                    reason="; ".join(mapping.errors),
                )
            else:
                state = repository.transition(
                    state,
                    to_status=WorkflowStatus.RUNNING,
                    to_phase=WorkflowPhase.MAPPED,
                    actor_type="agent",
                    actor_name=mapping.agent_name,
                    reason="No unresolved schema mappings remain.",
                )
            repository.save_checkpoint(state, "mapping-ontology", metadata={"status": mapping.status})

    payload = {
        "export_id": args.export_id,
        "workflow_run_created": created,
        "workflow_state": state.snapshot(),
        "agents": [asdict(result) for result in results],
    }
    json_path = write_json_report(args.export_id, "schema_agent_team_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "schema_agent_team_report.md",
        "Migration V2 Schema Agent Team Report",
        [
            (
                "Workflow",
                "\n".join(
                    [
                        f"- `run_id`: `{state.run_id}`",
                        f"- `status`: `{state.status}`",
                        f"- `phase`: `{state.current_phase}`",
                    ]
                ),
            ),
            (
                "Agents",
                "\n".join(
                    f"- `{result.agent_name}`: `{result.status}` mode=`{result.mode}` "
                    f"proposals={len(result.proposals)} tools={result.tools_used}"
                    for result in results
                ),
            ),
            (
                "Approval Requirements",
                json.dumps(state.approval_requirements, indent=2) if state.approval_requirements else "None.",
            ),
            (
                "Mapping Proposals",
                "\n".join(
                    f"- `{proposal.raw_table_name}.{proposal.raw_column_name}`: "
                    f"`{proposal.proposed_action}` confidence={proposal.confidence:.2f} - {proposal.rationale}"
                    for result in results
                    for proposal in result.proposals
                )
                or "None.",
            ),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
