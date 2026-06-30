from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from _common import (
    REPORT_ROOT,
    config_section,
    ensure_tables,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)


LOGGER = setup_logging("migration_v2.publish_graph_version")

REQUIRED_READY_REPORTS = {
    "conditional_publish": "conditional_publish_report.json",
    "trusted_graph_projection": "trusted_graph_projection_report.json",
    "candidate_search_activation": "candidate_search_activation_report.json",
    "fast_search_benchmark": "fast_search_benchmark_report.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish an approved migration_v2 graph version.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--approved-by", help="Human approver identifier. Required for real publish.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate gates without publishing.")
    return parser.parse_args()


def engine_from_args(args: argparse.Namespace):
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
        if v2_config.get("postgres_url"):
            return postgres_engine_from_url(v2_config["postgres_url"])
    return postgres_engine()


def load_report(export_id: str, filename: str) -> dict[str, Any] | None:
    path = REPORT_ROOT / export_id / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def collect_gate_evidence(engine, export_id: str) -> dict[str, Any]:
    ensure_tables(engine, ["migration_export_run", "migration_validation_finding", "migration_benchmark_result"])
    with engine.connect() as conn:
        open_errors = int(
            conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM migration_validation_finding
                    WHERE export_id = :export_id AND severity = 'ERROR' AND status = 'open'
                    """
                ),
                {"export_id": export_id},
            ).scalar_one()
        )
        benchmark_rows = int(
            conn.execute(
                text("SELECT count(*) FROM migration_benchmark_result WHERE export_id = :export_id"),
                {"export_id": export_id},
            ).scalar_one()
        )
        refresh_function = conn.execute(
            text("SELECT to_regprocedure('refresh_lineage_search_documents(text)')")
        ).scalar()
        search_state_exists = bool(
            conn.execute(
                text("SELECT to_regclass('public.lineage_search_state')")
            ).scalar()
        )
        previous_search_state = None
        if search_state_exists:
            row = conn.execute(
                text(
                    """
                    SELECT active_graph_version, document_count, published_at
                    FROM lineage_search_state
                    WHERE singleton = true
                    """
                )
            ).mappings().first()
            previous_search_state = dict(row) if row else None

    reports: dict[str, Any] = {}
    missing_reports: list[str] = []
    report_blockers: list[str] = []
    for report_key, filename in REQUIRED_READY_REPORTS.items():
        report = load_report(export_id, filename)
        if report is None:
            missing_reports.append(filename)
            continue
        reports[report_key] = {
            "filename": filename,
            "status": report.get("status"),
            "blockers": report.get("blockers") or [],
        }
        if report.get("status") != "ready":
            report_blockers.append(f"{filename} status is {report.get('status')!r}.")
        for blocker in report.get("blockers") or []:
            report_blockers.append(f"{filename}: {blocker}")

    blockers: list[str] = []
    if open_errors:
        blockers.append(f"{open_errors} open validation errors remain.")
    if benchmark_rows <= 0:
        blockers.append("No migration_benchmark_result rows exist for this export.")
    if missing_reports:
        blockers.append("Missing required publish reports: " + ", ".join(missing_reports))
    blockers.extend(report_blockers)
    if not refresh_function:
        blockers.append("refresh_lineage_search_documents() is not installed.")
    if not search_state_exists:
        blockers.append("lineage_search_state table is not installed.")
    if search_state_exists and previous_search_state is None:
        blockers.append("Rollback metadata cannot be captured because lineage_search_state has no singleton row.")

    return {
        "open_validation_errors": open_errors,
        "benchmark_rows": benchmark_rows,
        "refresh_function": str(refresh_function) if refresh_function else None,
        "search_state_exists": search_state_exists,
        "previous_search_state": previous_search_state,
        "reports": reports,
        "missing_reports": missing_reports,
        "blockers": blockers,
    }


def publish(engine, export_id: str, approved_by: str, gate_evidence: dict[str, Any]) -> dict[str, Any]:
    previous_state = gate_evidence.get("previous_search_state")
    with engine.begin() as conn:
        published = conn.execute(
            text("SELECT * FROM refresh_lineage_search_documents(:export_id)"),
            {"export_id": export_id},
        ).mappings().one()
        metadata = {
            "migration_v2_publish": {
                "approved_by": approved_by,
                "previous_search_state": previous_state,
                "published_search_state": dict(published),
                "required_reports": REQUIRED_READY_REPORTS,
            }
        }
        conn.execute(
            text(
                """
                UPDATE migration_export_run
                SET status = 'published',
                    completed_at = now(),
                    metadata = coalesce(metadata, '{}'::jsonb) || CAST(:metadata AS jsonb)
                WHERE export_id = :export_id
                """
            ),
            {"export_id": export_id, "metadata": json.dumps(metadata, default=str)},
        )
        conn.execute(
            text(
                """
                UPDATE migration_publication_snapshot
                SET status = 'published', rollback_metadata = CAST(:rollback AS jsonb)
                WHERE id = (
                    SELECT id FROM migration_publication_snapshot
                    WHERE export_id = :export_id AND status = 'ready'
                    ORDER BY created_at DESC LIMIT 1
                )
                """
            ),
            {"export_id": export_id, "rollback": json.dumps({"previous_search_state": previous_state}, default=str)},
        )
    return dict(published)


def sync_workflow_status(engine, export_id: str, publish_status: str, blockers: list[str]) -> None:
    if publish_status == "ready_to_publish":
        workflow_status, phase = "ready", "ready"
    elif publish_status == "published":
        workflow_status, phase = "published", "published"
    else:
        workflow_status, phase = "blocked", "queue_review"
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE migration_workflow_run
            SET status = :status,
                current_phase = :phase,
                state = jsonb_set(
                    jsonb_set(
                        jsonb_set(state, '{status}', to_jsonb(CAST(:status AS text)), true),
                        '{current_phase}', to_jsonb(CAST(:phase AS text)), true
                    ),
                    '{errors}', CAST(:errors AS jsonb), true
                ),
                completed_at = CASE WHEN :status = 'published' THEN now() ELSE completed_at END,
                updated_at = now()
            WHERE run_id = (
                SELECT run_id FROM migration_workflow_run
                WHERE export_id = :export_id ORDER BY updated_at DESC LIMIT 1
            )
        """), {
            "export_id": export_id,
            "status": workflow_status,
            "phase": phase,
            "errors": json.dumps([{"phase": "publish_gate", "message": item} for item in blockers]),
        })


def main() -> None:
    args = parse_args()
    engine = engine_from_args(args)
    gate_evidence = collect_gate_evidence(engine, args.export_id)
    blockers = list(gate_evidence["blockers"])

    if not args.dry_run and not args.approved_by:
        blockers.append("--approved-by is required for publish.")

    published_state = None
    status = "blocked" if blockers else "ready_to_publish"
    if not args.dry_run and not blockers:
        published_state = publish(engine, args.export_id, args.approved_by or "", gate_evidence)
        status = "published"
    sync_workflow_status(engine, args.export_id, status, blockers)

    payload = {
        "export_id": args.export_id,
        "status": status,
        "dry_run": args.dry_run,
        "approved_by": args.approved_by,
        "gate_evidence": gate_evidence,
        "published_state": published_state,
        "blockers": blockers,
    }
    json_path = write_json_report(args.export_id, "publish_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "publish_report.md",
        "Migration V2 Publish Report",
        [
            ("Status", f"`{status}`"),
            (
                "Gate Evidence",
                "\n".join(
                    [
                        f"- `open_validation_errors`: {gate_evidence['open_validation_errors']}",
                        f"- `benchmark_rows`: {gate_evidence['benchmark_rows']}",
                        f"- `refresh_function`: {gate_evidence['refresh_function']}",
                        f"- `search_state_exists`: {gate_evidence['search_state_exists']}",
                    ]
                ),
            ),
            (
                "Required Reports",
                "\n".join(
                    f"- `{name}`: `{report['status']}`"
                    for name, report in gate_evidence["reports"].items()
                )
                or "None.",
            ),
            (
                "Published State",
                json.dumps(published_state, indent=2, default=str) if published_state else "Not published.",
            ),
            (
                "Blockers",
                "\n".join(f"- {item}" for item in blockers) or "None.",
            ),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)
    if blockers and not args.dry_run:
        raise SystemExit("Publish blocked. See publish_report.md.")


if __name__ == "__main__":
    main()
