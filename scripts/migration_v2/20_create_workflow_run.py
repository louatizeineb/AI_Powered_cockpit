from __future__ import annotations

import argparse
import json

from _common import (
    config_section,
    ensure_tables,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)
from app.migration_v2.orchestration.repository import WorkflowRepository


LOGGER = setup_logging("migration_v2.create_workflow_run")
REQUIRED_TABLES = [
    "migration_export_run",
    "migration_raw_file",
    "migration_workflow_run",
    "migration_workflow_transition",
    "migration_workflow_checkpoint",
    "migration_approval_request",
    "migration_tool_execution",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or resume an idempotent migration_v2 workflow run.")
    parser.add_argument("--export-id", required=True)
    parser.add_argument("--env-config", help="Local environment config with a v2.postgres_url value.")
    parser.add_argument("--workflow-version", default="1.0.0")
    parser.add_argument("--trigger-type", default="manual", choices=["manual", "api", "schedule", "storage_event"])
    parser.add_argument("--created-by", default="cli")
    parser.add_argument("--trigger-payload", default="{}", help="JSON object describing the trigger.")
    return parser.parse_args()


def engine_from_args(args: argparse.Namespace):
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
        if v2_config.get("postgres_url"):
            return postgres_engine_from_url(v2_config["postgres_url"])
    return postgres_engine()


def main() -> None:
    args = parse_args()
    try:
        trigger_payload = json.loads(args.trigger_payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--trigger-payload must be valid JSON: {exc}") from exc
    if not isinstance(trigger_payload, dict):
        raise SystemExit("--trigger-payload must be a JSON object.")

    engine = engine_from_args(args)
    ensure_tables(engine, REQUIRED_TABLES)
    state, created = WorkflowRepository(engine).create_or_get_run(
        export_id=args.export_id,
        workflow_version=args.workflow_version,
        trigger_type=args.trigger_type,
        trigger_payload=trigger_payload,
        created_by=args.created_by,
    )
    payload = {
        "created": created,
        "idempotent_resume": not created,
        "state": state.snapshot(),
    }
    json_path = write_json_report(args.export_id, "workflow_run_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "workflow_run_report.md",
        "Migration V2 Workflow Run",
        [
            ("Result", f"`{'created' if created else 'existing run resumed'}`"),
            ("Run", f"- `run_id`: `{state.run_id}`\n- `thread_id`: `{state.thread_id}`"),
            (
                "Identity",
                "\n".join(
                    [
                        f"- `export_id`: `{state.export_id}`",
                        f"- `export_fingerprint`: `{state.export_fingerprint}`",
                        f"- `contract_version`: `{state.contract_version}`",
                        f"- `workflow_version`: `{state.workflow_version}`",
                    ]
                ),
            ),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
