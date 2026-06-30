from __future__ import annotations

import argparse
import json
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
from app.migration_v2.orchestration.persistent_orchestrator import PersistentSchemaOrchestrator


LOGGER = setup_logging("migration_v2.langgraph_schema_orchestrator")
SQL_FILES = [
    ROOT / "backend" / "migrations" / "sql" / "013_migration_v2_agent_runs.sql",
    ROOT / "backend" / "migrations" / "sql" / "015_migration_v2_schema_agents.sql",
    ROOT / "backend" / "migrations" / "sql" / "016_migration_v2_langgraph_orchestrator.sql",
]


def common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-config",
        default=str(ROOT / "configs" / "migration_v2" / "local_env.yaml"),
    )
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent LangGraph migration_v2 schema orchestrator.")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    common_arguments(start)
    start.add_argument("--export-id", required=True)
    start.add_argument("--created-by", default="langgraph-cli")
    start.add_argument("--require-llm", action="store_true")
    start.add_argument("--refresh-tools", action="store_true")

    resume = commands.add_parser("resume")
    common_arguments(resume)
    resume.add_argument("--run-id", required=True)
    decisions = resume.add_mutually_exclusive_group(required=True)
    decisions.add_argument("--decision-json")
    decisions.add_argument("--decision-file")

    status = commands.add_parser("status")
    common_arguments(status)
    status.add_argument("--run-id", required=True)
    return parser.parse_args()


def apply_sql(engine) -> None:
    with engine.begin() as conn:
        cursor = conn.connection.cursor()
        try:
            for path in SQL_FILES:
                cursor.execute(path.read_text(encoding="utf-8"))
        finally:
            cursor.close()


def decision_payload(args: argparse.Namespace) -> dict:
    raw = Path(args.decision_file).read_text(encoding="utf-8") if args.decision_file else args.decision_json
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Approval decision must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Approval decision must be a JSON object.")
    return payload


def write_report(export_id: str, response: dict) -> None:
    approval_template_path = None
    interrupts = response.get("interrupts") or []
    schema_interrupt = next(
        (item for item in interrupts if item.get("type") == "schema_mapping_review"),
        None,
    )
    if schema_interrupt:
        approval_template = {
            "decision": "approve",
            "decided_by": "replace-with-reviewer-id",
            "rationale": "Optional metadata columns are absent in this export; retain the contract and do not infer replacements.",
            "resolutions": [
                {
                    "raw_table_name": proposal["raw_table_name"],
                    "raw_column_name": proposal["raw_column_name"],
                    "action": "keep_contract_missing",
                }
                for proposal in schema_interrupt.get("proposals") or []
            ],
        }
        approval_template_path = write_json_report(
            export_id,
            "schema_mapping_approval_template.json",
            approval_template,
        )
    json_path = write_json_report(export_id, "langgraph_orchestrator_report.json", response)
    md_path = write_markdown_report(
        export_id,
        "langgraph_orchestrator_report.md",
        "Persistent LangGraph Orchestrator Report",
        [
            (
                "Workflow",
                "\n".join(
                    [
                        f"- `run_id`: `{response['run_id']}`",
                        f"- `status`: `{response['status']}`",
                        f"- `current_phase`: `{response['current_phase']}`",
                        f"- `next_nodes`: `{response['next_nodes']}`",
                    ]
                ),
            ),
            (
                "Interrupts",
                json.dumps(interrupts, indent=2),
            ),
            (
                "Approval Template",
                str(approval_template_path) if approval_template_path else "No active approval interrupt.",
            ),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


def main() -> None:
    args = parse_args()
    config = load_env_config(args.env_config)
    postgres_url = str(config_section(config, "v2")["postgres_url"])
    engine = postgres_engine_from_url(postgres_url)
    apply_sql(engine)
    orchestrator = PersistentSchemaOrchestrator(
        engine=engine,
        postgres_url=postgres_url,
        env_config_path=args.env_config,
        contract_path=args.contract,
        require_llm=getattr(args, "require_llm", False),
        refresh_tools=getattr(args, "refresh_tools", False),
    )
    if args.command == "start":
        response = orchestrator.start(args.export_id, created_by=args.created_by)
    elif args.command == "resume":
        response = orchestrator.resume(args.run_id, decision_payload(args))
    else:
        response = orchestrator.status(args.run_id)
    export_id = str(response["state"]["export_id"])
    write_report(export_id, response)
    print(json.dumps({key: response[key] for key in ["run_id", "status", "current_phase", "next_nodes"]}, indent=2))


if __name__ == "__main__":
    main()
