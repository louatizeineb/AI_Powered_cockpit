from __future__ import annotations

import json
import os
import subprocess
import sys
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.migration_v2.agents.manifests import get_agent_manifest
from app.migration_v2.orchestration.tool_contracts import ToolInput, validate_tool_input


ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class ToolSpec:
    script: str
    version: str
    artifacts: tuple[str, ...]


TOOL_SPECS = {
    "register_export": ToolSpec("01_register_export.py", "1.0.0", ("registration_report.json",)),
    "profile_export": ToolSpec("02_profile_export.py", "1.0.0", ("profile_report.json",)),
    "detect_schema_drift": ToolSpec("03_detect_schema_drift.py", "1.0.0", ("schema_drift_report.json",)),
    "generate_mapping_plan": ToolSpec("04_generate_mapping_plan.py", "1.0.0", ("mapping_plan.json",)),
    "preprocess_staging": ToolSpec("05_preprocess_to_staging.py", "1.0.0", ("staging_preprocess_report.json",)),
    "validate_staging": ToolSpec("06_validate_staging.py", "1.0.0", ("validation_report.json",)),
    "populate_validation_queue": ToolSpec("16_populate_validation_queue.py", "1.0.0", ("validation_queue_report.json",)),
    "build_conditional_projection": ToolSpec("24_build_conditional_publish_projection.py", "1.0.0", ("conditional_publish_report.json",)),
    "build_candidate_graph": ToolSpec("07_build_graph.py", "2.0.0", ("graph_build_report.json",)),
    "generate_lineage_paths": ToolSpec("08_generate_lineage_paths.py", "2.0.0", ("lineage_path_report.json",)),
    "audit_candidate_graph": ToolSpec(
        "09_audit_and_compare.py",
        "2.0.0",
        ("graph_audit_report.json", "relationship_parity_report.json", "audit_compare_report.json"),
    ),
    "run_search_benchmark": ToolSpec("13_benchmark_fast_search.py", "1.0.0", ("fast_search_benchmark_report.json",)),
    "activate_candidate_search": ToolSpec(
        "25_activate_candidate_search.py", "1.0.0", ("candidate_search_activation_report.json",)
    ),
    "resolve_structural_parity": ToolSpec(
        "26_resolve_structural_parity.py", "1.0.0", ("structural_parity_resolution_report.json",)
    ),
    "enforce_trusted_graph_projection": ToolSpec(
        "27_enforce_trusted_graph_projection.py", "1.0.0", ("trusted_graph_projection_report.json",)
    ),
    "publish_graph_version": ToolSpec("10_publish_graph_version.py", "2.0.0", ("publish_report.json",)),
    "build_schema_intelligence_kg": ToolSpec(
        "21_build_schema_intelligence_kg.py",
        "1.0.0",
        ("schema_intelligence_projection.json", "schema_intelligence_kg_report.json"),
    ),
    "evaluate_validation_agent": ToolSpec(
        "28_evaluate_validation_agents.py",
        "1.1.0",
        ("agent_evaluation_report.json", "agent_evaluation_report.md"),
    ),
}


class AllowlistedToolRuntime:
    def __init__(self, workflow_repository, state, postgres_url: str):
        self.repository = workflow_repository
        self.state = state
        self.postgres_url = postgres_url

    def execute(
        self,
        *,
        agent_name: str,
        tool_name: str,
        payload: ToolInput | dict[str, Any],
        refresh: bool = False,
    ) -> dict[str, Any]:
        manifest = get_agent_manifest(agent_name)
        if tool_name not in manifest.allowed_tools:
            raise PermissionError(f"{agent_name} is not allowed to execute {tool_name}.")
        try:
            spec = TOOL_SPECS[tool_name]
        except KeyError as exc:
            raise PermissionError(f"Tool is not registered: {tool_name}") from exc

        typed_input = validate_tool_input(tool_name, payload)
        arguments = typed_input.cli_arguments()
        input_payload = {"parameters": typed_input.model_dump(mode="json"), "refresh": refresh}
        if refresh:
            input_payload["execution_nonce"] = str(uuid4())
        execution_id, created = self.repository.start_tool_execution(
            self.state,
            tool_name=tool_name,
            tool_version=spec.version + ("-refresh" if refresh else ""),
            input_payload=input_payload,
            agent_name=agent_name,
        )
        if not created:
            existing = self.repository.get_tool_execution(execution_id)
            if existing["status"] == "completed":
                return {"status": "skipped_idempotent", "execution_id": execution_id, **existing}
            raise RuntimeError(f"Existing tool execution {execution_id} is {existing['status']}.")

        before_effects = self._database_snapshot()
        command = [sys.executable, str(ROOT / "scripts" / "migration_v2" / spec.script), *arguments]
        env = os.environ.copy()
        env["POSTGRES_URL"] = self.postgres_url
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        output = {
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-3000:],
            "stderr_tail": completed.stderr[-3000:],
        }
        artifacts = [
            str(ROOT / "reports" / "migration_v2" / self.state.export_id / name)
            for name in spec.artifacts
            if (ROOT / "reports" / "migration_v2" / self.state.export_id / name).exists()
        ]
        after_effects = self._database_snapshot()
        database_effects = self._database_effects(before_effects, after_effects)
        if completed.returncode == 0:
            self.repository.finish_tool_execution(
                execution_id,
                status="completed",
                output_payload=output,
                generated_artifacts=artifacts,
                database_effects=database_effects,
            )
            return {"status": "completed", "execution_id": execution_id, **output}

        self.repository.finish_tool_execution(
            execution_id,
            status="failed",
            output_payload=output,
            database_effects=database_effects,
            error={"message": completed.stderr[-3000:] or completed.stdout[-3000:]},
        )
        raise RuntimeError(f"Tool {tool_name} failed: {output['stderr_tail'] or output['stdout_tail']}")

    def run(self, *, agent_name: str, tool_name: str, arguments: list[str], refresh: bool = False) -> dict[str, Any]:
        """Compatibility shim for existing agents; rejects unknown CLI flags through the typed model."""
        parsed: dict[str, Any] = {}
        index = 0
        while index < len(arguments):
            token = arguments[index]
            if not token.startswith("--"):
                raise ValueError(f"Unexpected positional tool argument: {token}")
            key = token[2:].replace("-", "_")
            if index + 1 < len(arguments) and not arguments[index + 1].startswith("--"):
                parsed[key] = arguments[index + 1]
                index += 2
            else:
                parsed[key] = True
                index += 1
        return self.execute(agent_name=agent_name, tool_name=tool_name, payload=parsed, refresh=refresh)

    def _database_snapshot(self) -> dict[str, int]:
        tables = (
            "migration_raw_file", "migration_column_profile", "migration_mapping_decision",
            "catalog_object_staging", "catalog_relationship_staging", "migration_validation_finding",
            "migration_validation_queue", "lineage_path", "migration_benchmark_result",
            "migration_publication_snapshot", "migration_agent_eval_case", "migration_agent_eval_run",
            "migration_agent_eval_score", "migration_agent_evidence_plan",
        )
        snapshot: dict[str, int] = {}
        with self.repository.engine.connect() as conn:
            for table in tables:
                exists = conn.execute(text("SELECT to_regclass(:table_name)"), {"table_name": f"public.{table}"}).scalar()
                if exists:
                    snapshot[table] = int(conn.execute(
                        text(f"SELECT count(*) FROM {table} WHERE export_id = :export_id"),
                        {"export_id": self.state.export_id},
                    ).scalar_one())
        return snapshot

    @staticmethod
    def _database_effects(before: dict[str, int], after: dict[str, int]) -> dict[str, Any]:
        return {
            "before": before,
            "after": after,
            "delta": {name: after.get(name, 0) - before.get(name, 0) for name in sorted(set(before) | set(after))},
        }
