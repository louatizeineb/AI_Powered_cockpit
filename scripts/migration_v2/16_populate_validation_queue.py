from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

from _common import (
    REPORT_ROOT,
    ROOT,
    config_section,
    ensure_tables,
    json_param,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)


LOGGER = setup_logging("migration_v2.validation_queue")

QUEUE_SQL = ROOT / "backend" / "migrations" / "sql" / "012_migration_v2_validation_queue.sql"
NON_BLOCKING_POLICIES = {"accept", "exclude", "quarantine"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Populate migration_v2 validation queue from decision-layer anomalies.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    parser.add_argument(
        "--approve-proposed-quarantine",
        action="store_true",
        help="Approve currently proposed quarantine items after queue population.",
    )
    parser.add_argument("--approved-by", help="Required with --approve-proposed-quarantine.")
    parser.add_argument("--approval-rationale", default="", help="Approval rationale for proposed quarantine items.")
    return parser.parse_args()


def engine_from_args(args: argparse.Namespace):
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
        if v2_config.get("postgres_url"):
            return postgres_engine_from_url(v2_config["postgres_url"])
    return postgres_engine()


def apply_queue_schema(engine) -> None:
    sql = QUEUE_SQL.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)


def as_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def as_list(value: Any) -> list[Any]:
    parsed = as_json(value)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def issue_hash(*parts: str | None) -> str:
    digest = hashlib.sha256()
    digest.update("|".join(part or "" for part in parts).encode("utf-8"))
    return digest.hexdigest()[:16]


def fetch_rows(engine, sql: str, export_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"export_id": export_id}).mappings().all()
    return [dict(row) for row in rows]


def build_role_issues(engine, export_id: str) -> list[dict[str, Any]]:
    rows = fetch_rows(
        engine,
        """
        SELECT node_id, observed_roles, canonical_role, conflict_fields,
               decision_status, decision_reason, evidence
        FROM migration_role_resolution
        WHERE export_id = :export_id AND decision_status <> 'accepted'
        ORDER BY node_id
        """,
        export_id,
    )
    issues: list[dict[str, Any]] = []
    for row in rows:
        evidence = as_json(row.get("evidence")) or {}
        conflict_fields = [str(value) for value in as_list(row.get("conflict_fields"))]
        issue_type = "duplicate_role_path_conflict" if "path_full" in conflict_fields else "duplicate_role_needs_review"
        issue_id = f"role:{row['node_id']}"
        issues.append(
            queue_issue(
                export_id=export_id,
                issue_id=issue_id,
                issue_type=issue_type,
                entity_kind="node",
                node_id=row["node_id"],
                severity="medium",
                confidence=0.75,
                publish_policy="needs_human",
                queue_status="pending",
                source_report="role_resolution_report.json",
                source_decision_status=row["decision_status"],
                proposed_action="Accept as alias/move, repair path/role metadata, or exclude from trusted graph.",
                rationale=row.get("decision_reason"),
                evidence={
                    **evidence,
                    "observed_roles": as_list(row.get("observed_roles")),
                    "canonical_role": row.get("canonical_role"),
                    "conflict_fields": conflict_fields,
                },
            )
        )
    return issues


def build_orphan_issues(engine, export_id: str) -> list[dict[str, Any]]:
    rows = fetch_rows(
        engine,
        """
        SELECT node_id, object_type, orphan_class, decision_status, decision_reason,
               child_count, relationship_count, evidence
        FROM migration_orphan_classification
        WHERE export_id = :export_id AND decision_status <> 'accepted'
        ORDER BY orphan_class, node_id
        """,
        export_id,
    )
    issues: list[dict[str, Any]] = []
    for row in rows:
        evidence = as_json(row.get("evidence")) or {}
        orphan_class = str(row["orphan_class"])
        placeholder = orphan_class == "placeholder_path_missing_parent_metadata"
        severity = "high" if placeholder else "medium"
        issue_id = f"orphan:{row['node_id']}"
        issues.append(
            queue_issue(
                export_id=export_id,
                issue_id=issue_id,
                issue_type=orphan_class,
                entity_kind="node",
                node_id=row["node_id"],
                severity=severity,
                confidence=0.7 if placeholder else 0.8,
                publish_policy="quarantine",
                queue_status="proposed",
                source_report="orphan_classification_decision_report.json",
                source_decision_status=row["decision_status"],
                proposed_action=(
                    "Quarantine from trusted hierarchy/search until parent metadata is repaired or human accepts root status."
                ),
                rationale=row.get("decision_reason"),
                evidence={
                    **evidence,
                    "object_type": row.get("object_type"),
                    "child_count": int(row.get("child_count") or 0),
                    "relationship_count": int(row.get("relationship_count") or 0),
                },
            )
        )
    return issues


def build_relationship_issues(engine, export_id: str) -> list[dict[str, Any]]:
    rows = fetch_rows(
        engine,
        """
        SELECT relationship_type, baseline_value, v2_value, delta_value, parity_status,
               decision_status, explanation_class, inverse_relationship_type,
               raw_link_types, decision_reason, required_action, evidence
        FROM migration_relationship_explanation
        WHERE export_id = :export_id AND decision_status <> 'accepted'
        ORDER BY relationship_type
        """,
        export_id,
    )
    issues: list[dict[str, Any]] = []
    for row in rows:
        rel_type = str(row["relationship_type"])
        if rel_type == "Relationships":
            publish_policy = "accept"
            queue_status = "proposed"
            severity = "low"
            proposed_action = "Accept aggregate-only v0 limitation after documenting edge/type breakdown gap."
        elif rel_type == "HAS_FIELD":
            publish_policy = "repair"
            queue_status = "pending"
            severity = "high"
            proposed_action = "Find the exact missing hierarchy edge and repair it or explicitly exclude it."
        elif rel_type == "IMPLEMENTS":
            publish_policy = "needs_human"
            queue_status = "pending"
            severity = "high"
            proposed_action = "Generate missing semantic edge list and classify each edge as repair, exclude, or baseline-only."
        else:
            publish_policy = "needs_human"
            queue_status = "pending"
            severity = "medium"
            proposed_action = row.get("required_action") or "Review relationship parity issue."
        evidence = as_json(row.get("evidence")) or {}
        issue_id = f"relationship:{rel_type}:{issue_hash(str(row.get('delta_value')))}"
        issues.append(
            queue_issue(
                export_id=export_id,
                issue_id=issue_id,
                issue_type=f"relationship_{row['explanation_class']}",
                entity_kind="relationship_type",
                relationship_type=rel_type,
                severity=severity,
                confidence=0.8,
                publish_policy=publish_policy,
                queue_status=queue_status,
                source_report="relationship_explanation_decision_report.json",
                source_decision_status=row["decision_status"],
                proposed_action=proposed_action,
                rationale=row.get("decision_reason"),
                evidence={
                    **evidence,
                    "baseline_value": decimal_to_float(row.get("baseline_value")),
                    "v2_value": decimal_to_float(row.get("v2_value")),
                    "delta_value": decimal_to_float(row.get("delta_value")),
                    "parity_status": row.get("parity_status"),
                    "inverse_relationship_type": row.get("inverse_relationship_type"),
                    "raw_link_types": as_list(row.get("raw_link_types")),
                    "required_action": row.get("required_action"),
                },
            )
        )
    return issues


def decimal_to_float(value: Any) -> Any:
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if value == as_int else float(value)
    return value


def queue_issue(
    *,
    export_id: str,
    issue_id: str,
    issue_type: str,
    entity_kind: str,
    severity: str,
    confidence: float,
    publish_policy: str,
    queue_status: str,
    source_report: str,
    source_decision_status: str,
    proposed_action: str,
    rationale: str | None,
    evidence: dict[str, Any],
    node_id: str | None = None,
    src_node_id: str | None = None,
    tgt_node_id: str | None = None,
    relationship_type: str | None = None,
) -> dict[str, Any]:
    return {
        "export_id": export_id,
        "issue_id": issue_id,
        "issue_type": issue_type,
        "entity_kind": entity_kind,
        "node_id": node_id,
        "src_node_id": src_node_id,
        "tgt_node_id": tgt_node_id,
        "relationship_type": relationship_type,
        "severity": severity,
        "confidence": confidence,
        "publish_policy": publish_policy,
        "queue_status": queue_status,
        "source_report": source_report,
        "source_decision_status": source_decision_status,
        "proposed_action": proposed_action,
        "rationale": rationale,
        "evidence": json_param(evidence),
    }


def upsert_queue(engine, export_id: str, issues: list[dict[str, Any]]) -> None:
    with engine.begin() as conn:
        if not issues:
            return
        columns = list(issues[0])
        insert_values = ", ".join(f":{column}" for column in columns)
        update_columns = [
            "issue_type",
            "entity_kind",
            "node_id",
            "src_node_id",
            "tgt_node_id",
            "relationship_type",
            "severity",
            "confidence",
            "source_report",
            "source_decision_status",
            "proposed_by",
            "proposed_action",
            "rationale",
            "evidence",
        ]
        updates = ", ".join(
            [
                "queue_status = CASE WHEN migration_validation_queue.queue_status IN ('approved', 'resolved') "
                "THEN migration_validation_queue.queue_status ELSE EXCLUDED.queue_status END",
                "publish_policy = CASE WHEN migration_validation_queue.queue_status IN ('approved', 'resolved') "
                "THEN migration_validation_queue.publish_policy ELSE EXCLUDED.publish_policy END",
                *[f"{column} = EXCLUDED.{column}" for column in update_columns if column in columns],
                "updated_at = now()",
            ]
        )
        conn.execute(
            text(
                f"""
                INSERT INTO migration_validation_queue ({', '.join(columns)})
                VALUES ({insert_values})
                ON CONFLICT (export_id, issue_id)
                DO UPDATE SET {updates}
                """
            ),
            issues,
        )
        conn.execute(
            text(
                """
                UPDATE migration_validation_queue
                SET queue_status = 'resolved', resolved_at = coalesce(resolved_at, now()), updated_at = now()
                WHERE export_id = :export_id
                  AND issue_id <> ALL(:active_issue_ids)
                  AND queue_status NOT IN ('resolved', 'approved')
                """
            ),
            {"export_id": export_id, "active_issue_ids": [issue["issue_id"] for issue in issues]},
        )


def approve_proposed_quarantine(engine, export_id: str, approved_by: str, rationale: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE migration_validation_queue
                SET queue_status = 'approved',
                    approved_by = :approved_by,
                    approved_at = now(),
                    rationale = coalesce(nullif(rationale, ''), :rationale),
                    updated_at = now()
                WHERE export_id = :export_id
                  AND publish_policy = 'quarantine'
                  AND queue_status = 'proposed'
                """
            ),
            {"export_id": export_id, "approved_by": approved_by, "rationale": rationale},
        )
    return int(result.rowcount or 0)


def collect_queue_report(engine, export_id: str) -> dict[str, Any]:
    rows = fetch_rows(
        engine,
        """
        SELECT issue_id, issue_type, entity_kind, node_id, relationship_type, severity,
               publish_policy, queue_status, source_report, proposed_action, rationale, evidence
        FROM migration_validation_queue
        WHERE export_id = :export_id
        ORDER BY severity DESC, publish_policy, issue_type, issue_id
        """,
        export_id,
    )
    policy_counts = Counter(f"{row['publish_policy']}:{row['queue_status']}" for row in rows)
    issue_type_counts = Counter(str(row["issue_type"]) for row in rows)
    severity_counts = Counter(str(row["severity"]) for row in rows)

    blocking_rows = [row for row in rows if queue_row_blocks_publish(row)]
    nonblocking_rows = [row for row in rows if not queue_row_blocks_publish(row)]
    blockers = []
    if blocking_rows:
        grouped = Counter(f"{row['publish_policy']}:{row['queue_status']}" for row in blocking_rows)
        blockers.extend(f"{count} queue items remain `{key}`." for key, count in sorted(grouped.items()))

    return {
        "export_id": export_id,
        "status": "ready" if not blocking_rows else "blocked",
        "total_queue_items": len(rows),
        "blocking_item_count": len(blocking_rows),
        "nonblocking_item_count": len(nonblocking_rows),
        "policy_status_counts": dict(sorted(policy_counts.items())),
        "issue_type_counts": dict(issue_type_counts.most_common()),
        "severity_counts": dict(sorted(severity_counts.items())),
        "blockers": blockers,
        "blocking_samples": [queue_sample(row) for row in blocking_rows[:50]],
        "nonblocking_samples": [queue_sample(row) for row in nonblocking_rows[:25]],
    }


def queue_row_blocks_publish(row: dict[str, Any]) -> bool:
    policy = str(row["publish_policy"])
    status = str(row["queue_status"])
    if policy == "block":
        return True
    if policy == "repair":
        return status != "resolved"
    if policy == "needs_human":
        return True
    if policy in NON_BLOCKING_POLICIES:
        return status not in {"approved", "resolved"}
    return True


def queue_sample(row: dict[str, Any]) -> dict[str, Any]:
    evidence = as_json(row.get("evidence")) or {}
    return {
        "issue_id": row["issue_id"],
        "issue_type": row["issue_type"],
        "severity": row["severity"],
        "publish_policy": row["publish_policy"],
        "queue_status": row["queue_status"],
        "node_id": row.get("node_id"),
        "relationship_type": row.get("relationship_type"),
        "proposed_action": row.get("proposed_action"),
        "path_full": evidence.get("path_full"),
        "paths": evidence.get("paths"),
    }


def write_queue_csv(export_id: str, payload: dict[str, Any], engine) -> Path:
    out_dir = REPORT_ROOT / export_id / "manual_review_csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "09_validation_queue.csv"
    with engine.connect() as conn:
        proposal_table_exists = bool(
            conn.execute(text("SELECT to_regclass('public.migration_agent_proposal')")).scalar()
        )
    if proposal_table_exists:
        rows = fetch_rows(
            engine,
            """
            WITH latest_proposal AS (
                SELECT DISTINCT ON (issue_id)
                    issue_id,
                    proposed_policy AS agent_proposed_policy,
                    confidence AS agent_confidence,
                    rationale AS agent_rationale,
                    human_question AS agent_question
                FROM migration_agent_proposal
                WHERE export_id = :export_id
                ORDER BY issue_id, created_at DESC, id DESC
            )
            SELECT q.issue_id, q.issue_type, q.entity_kind, q.node_id, q.relationship_type,
                   q.severity, q.confidence, q.publish_policy, q.queue_status, q.source_report,
                   q.source_decision_status, q.proposed_action, q.rationale, q.approved_by,
                   q.approved_at, q.resolved_at, q.evidence,
                   p.agent_proposed_policy, p.agent_confidence, p.agent_rationale, p.agent_question
            FROM migration_validation_queue q
            LEFT JOIN latest_proposal p ON p.issue_id = q.issue_id
            WHERE q.export_id = :export_id
            ORDER BY q.queue_status, q.publish_policy, q.issue_type, q.issue_id
            """,
            export_id,
        )
    else:
        rows = fetch_rows(
            engine,
            """
            SELECT issue_id, issue_type, entity_kind, node_id, relationship_type, severity,
                   confidence, publish_policy, queue_status, source_report, source_decision_status,
                   proposed_action, rationale, approved_by, approved_at, resolved_at, evidence,
                   NULL AS agent_proposed_policy,
                   NULL AS agent_confidence,
                   NULL AS agent_rationale,
                   NULL AS agent_question
            FROM migration_validation_queue
            WHERE export_id = :export_id
            ORDER BY queue_status, publish_policy, issue_type, issue_id
            """,
            export_id,
        )
    columns = [
        "issue_id",
        "issue_type",
        "entity_kind",
        "node_id",
        "relationship_type",
        "severity",
        "confidence",
        "publish_policy",
        "queue_status",
        "source_report",
        "source_decision_status",
        "proposed_action",
        "rationale",
        "approved_by",
        "approved_at",
        "resolved_at",
        "evidence_summary",
        "agent_proposed_policy",
        "agent_confidence",
        "agent_rationale",
        "agent_question",
        "reviewer_decision",
        "reviewer_notes",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            evidence = as_json(row.get("evidence")) or {}
            row = dict(row)
            row["evidence_summary"] = json.dumps(
                {
                    key: evidence.get(key)
                    for key in [
                        "object_type",
                        "canonical_role",
                        "observed_roles",
                        "conflict_fields",
                        "path_full",
                        "paths",
                        "relationship_count",
                        "delta_value",
                        "required_action",
                    ]
                    if key in evidence
                },
                ensure_ascii=False,
                default=str,
            )
            row["reviewer_decision"] = ""
            row["reviewer_notes"] = ""
            writer.writerow(row)
    return path


def write_reports(export_id: str, payload: dict[str, Any], csv_path: Path) -> None:
    write_json_report(export_id, "validation_queue_report.json", payload)
    write_markdown_report(
        export_id,
        "validation_queue_report.md",
        "Migration V2 Validation Queue Report",
        [
            ("Status", f"`{payload['status']}`"),
            (
                "Counts",
                "\n".join(
                    [
                        f"- `total_queue_items`: {payload['total_queue_items']}",
                        f"- `blocking_item_count`: {payload['blocking_item_count']}",
                        f"- `nonblocking_item_count`: {payload['nonblocking_item_count']}",
                        f"- `policy_status_counts`: `{payload['policy_status_counts']}`",
                        f"- `issue_type_counts`: `{payload['issue_type_counts']}`",
                        f"- `severity_counts`: `{payload['severity_counts']}`",
                    ]
                ),
            ),
            ("CSV", f"`{csv_path}`"),
            ("Blockers", "\n".join(f"- {item}" for item in payload["blockers"]) or "None."),
        ],
    )


def main() -> None:
    args = parse_args()
    if args.approve_proposed_quarantine and not args.approved_by:
        raise SystemExit("--approve-proposed-quarantine requires --approved-by.")

    engine = engine_from_args(args)
    ensure_tables(
        engine,
        [
            "migration_export_run",
            "migration_role_resolution",
            "migration_orphan_classification",
            "migration_relationship_explanation",
        ],
    )
    apply_queue_schema(engine)

    issues = [
        *build_role_issues(engine, args.export_id),
        *build_orphan_issues(engine, args.export_id),
        *build_relationship_issues(engine, args.export_id),
    ]
    upsert_queue(engine, args.export_id, issues)

    if args.approve_proposed_quarantine:
        count = approve_proposed_quarantine(
            engine,
            args.export_id,
            args.approved_by,
            args.approval_rationale or "Approved bounded quarantine exceptions for publish governance.",
        )
        LOGGER.info("Approved %s proposed quarantine items", count)

    payload = collect_queue_report(engine, args.export_id)
    csv_path = write_queue_csv(args.export_id, payload, engine)
    write_reports(args.export_id, payload, csv_path)
    LOGGER.info("Validation queue populated with status %s", payload["status"])


if __name__ == "__main__":
    main()
