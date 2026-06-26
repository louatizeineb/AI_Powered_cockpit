from __future__ import annotations

from pathlib import Path

from app.migration_v2.agents.execution import AgentContext, ExecutableAgentResult
from app.migration_v2.agents.manifests import get_agent_manifest
from app.migration_v2.agents.persistence import AgentExecutionRepository


AGENT_ROLE = get_agent_manifest("ExportIntakeAgent")


def run(context: AgentContext) -> ExecutableAgentResult:
    persistence = AgentExecutionRepository(context.engine)
    agent_run_id = persistence.start(
        export_id=context.state.export_id,
        workflow_run_id=context.state.run_id,
        agent_name=AGENT_ROLE.name,
        mode="deterministic",
    )
    errors: list[str] = []
    evidence = context.workflow_repository.registered_export_evidence(context.state.export_id)
    files = evidence["files"]
    missing_files = [row["file_path"] for row in files if not Path(str(row["file_path"])).exists()]
    missing_hashes = [row["file_path"] for row in files if not row.get("file_hash")]
    raw_tables = [str(row["raw_table_name"]) for row in files]
    duplicate_tables = sorted({table for table in raw_tables if raw_tables.count(table) > 1})
    if missing_files:
        errors.append(f"{len(missing_files)} registered files are missing from disk.")
    if missing_hashes:
        errors.append(f"{len(missing_hashes)} registered files have no content hash.")
    status = "blocked" if errors else ("needs_approval" if duplicate_tables else "completed")
    result = ExecutableAgentResult(
        export_id=context.state.export_id,
        workflow_run_id=context.state.run_id,
        agent_name=AGENT_ROLE.name,
        status=status,
        mode="deterministic",
        summary={
            "reviewed_count": len(files),
            "file_count": len(files),
            "raw_tables": sorted(set(raw_tables)),
            "duplicate_raw_tables": duplicate_tables,
            "missing_files": missing_files,
            "missing_hashes": missing_hashes,
            "export_fingerprint": context.state.export_fingerprint,
        },
        errors=errors,
    )
    persistence.finish(agent_run_id, result)
    return result
