from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text

from app.migration_v2.agents.execution import AgentContext, ExecutableAgentResult
from app.migration_v2.agents.manifests import get_agent_manifest
from app.migration_v2.agents.persistence import AgentExecutionRepository


AGENT_ROLE = get_agent_manifest("SchemaIntelligenceAgent")


def run(context: AgentContext) -> ExecutableAgentResult:
    persistence = AgentExecutionRepository(context.engine)
    agent_run_id = persistence.start(
        export_id=context.state.export_id,
        workflow_run_id=context.state.run_id,
        agent_name=AGENT_ROLE.name,
        mode="deterministic_tools",
    )
    tools_used: list[str] = []
    errors: list[str] = []
    try:
        with context.engine.connect() as conn:
            profile_count = int(
                conn.execute(
                    text("SELECT count(*) FROM migration_column_profile WHERE export_id = :export_id"),
                    {"export_id": context.state.export_id},
                ).scalar_one()
            )
        if profile_count == 0 or context.refresh_tools:
            context.tool_runtime.execute(
                agent_name=AGENT_ROLE.name,
                tool_name="profile_export",
                payload={"export_id": context.state.export_id},
                refresh=context.refresh_tools,
            )
            tools_used.append("profile_export")

        context.tool_runtime.execute(
            agent_name=AGENT_ROLE.name,
            tool_name="build_schema_intelligence_kg",
            payload={
                "export_id": context.state.export_id,
                "env_config": context.env_config_path,
                "contract": context.contract_path,
            },
            refresh=context.refresh_tools,
        )
        tools_used.append("build_schema_intelligence_kg")
        report_path = (
            Path(__file__).resolve().parents[4]
            / "reports"
            / "migration_v2"
            / context.state.export_id
            / "schema_intelligence_kg_report.json"
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        graph_audit = report.get("graph_audit") or {}
        projection = report.get("projection") or {}
        status = "completed" if graph_audit.get("status") == "ready" else "blocked"
        if status == "blocked":
            errors.append("Schema Intelligence KG audit is not ready.")
        summary = {
            "reviewed_count": int(projection.get("column_count") or 0),
            "projection": projection,
            "graph_audit": graph_audit,
        }
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        summary = {"reviewed_count": 0}
        errors.append(str(exc))

    result = ExecutableAgentResult(
        export_id=context.state.export_id,
        workflow_run_id=context.state.run_id,
        agent_name=AGENT_ROLE.name,
        status=status,
        mode="deterministic_tools",
        summary=summary,
        tools_used=tools_used,
        errors=errors,
    )
    persistence.finish(agent_run_id, result)
    return result
