from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal
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
    write_markdown_report,
)


LOGGER = setup_logging("migration_v2.anomaly_review_tables")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export CSV review tables for migration_v2 publish anomalies.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    return parser.parse_args()


def engine_from_args(args: argparse.Namespace):
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
        if v2_config.get("postgres_url"):
            return postgres_engine_from_url(v2_config["postgres_url"])
    return postgres_engine()


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


def join_values(values: list[Any]) -> str:
    return " | ".join(str(value) for value in values if value is not None)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def path_parts(path: str | None) -> list[str]:
    if not path:
        return []
    return [part.strip() for part in path.replace("/", "\\").split("\\") if part.strip()]


def path_leaf(path: str | None) -> str:
    parts = path_parts(path)
    return parts[-1] if parts else ""


def common_prefix_len(paths: list[str]) -> int:
    if len(paths) < 2:
        return 0
    parts_list = [path_parts(path) for path in paths]
    count = 0
    for values in zip(*parts_list):
        if len({normalize_text(value) for value in values}) != 1:
            return count
        count += 1
    return count


def classify_path_pattern(paths: list[str]) -> str:
    if not paths:
        return "no_path"
    if any(all(part.lower() == "null" for part in path_parts(path)) for path in paths):
        return "placeholder_null_path"
    if len(paths) == 1:
        return "single_path"
    normalized = {normalize_text(path) for path in paths}
    if len(normalized) == 1:
        return "formatting_only"
    leaves = {normalize_text(path_leaf(path)) for path in paths}
    joined = " ".join(paths).lower()
    if len(leaves) == 1 and ("ontologie " in joined or "ontologies " in joined):
        return "same_leaf_ontology_folder_variant"
    if len(leaves) == 1:
        return "same_leaf_different_parent_path"
    if "dictionnaire des ontologies" in joined and "usages opérationnels" in joined:
        return "ontology_vs_operational_usage"
    return "different_path_or_label"


def review_dir(export_id: str) -> Path:
    path = REPORT_ROOT / export_id / "manual_review_csv"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def fetch_rows(engine, sql: str, export_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"export_id": export_id}).mappings().all()
    return [dict(row) for row in rows]


def role_rows(engine, export_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = fetch_rows(
        engine,
        """
        SELECT node_id, observed_roles, canonical_role, retained_roles, conflict_fields,
               decision_status, decision_reason, evidence
        FROM migration_role_resolution
        WHERE export_id = :export_id
        ORDER BY decision_status DESC, node_id
        """,
        export_id,
    )
    flat: list[dict[str, Any]] = []
    for row in rows:
        evidence = as_json(row.get("evidence")) or {}
        roles = [str(value) for value in as_list(row.get("observed_roles"))]
        conflict_fields = [str(value) for value in as_list(row.get("conflict_fields"))]
        paths = [str(value) for value in evidence.get("paths") or []]
        labels = [str(value) for value in evidence.get("labels") or []]
        technical_names = [str(value) for value in evidence.get("technical_names") or []]
        parent_ids = [str(value) for value in evidence.get("parent_node_ids") or []]
        source_tables = [str(value) for value in evidence.get("source_tables") or []]
        flat.append(
            {
                "node_id": row["node_id"],
                "decision_status": row["decision_status"],
                "canonical_role": row.get("canonical_role"),
                "observed_roles": join_values(roles),
                "role_pair": " + ".join(roles),
                "conflict_fields": join_values(conflict_fields),
                "decision_reason": row.get("decision_reason"),
                "source_tables": join_values(source_tables),
                "parent_node_ids": join_values(parent_ids),
                "parent_count": len(parent_ids),
                "paths": join_values(paths),
                "path_count": len(paths),
                "path_pattern": classify_path_pattern(paths),
                "common_prefix_segments": common_prefix_len(paths),
                "labels": join_values(labels),
                "technical_names": join_values(technical_names),
                "suggested_manual_decision": "",
                "reviewer_notes": "",
            }
        )

    summary_counter: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)
    for row in flat:
        key = (
            row["role_pair"],
            row["decision_status"],
            row["conflict_fields"],
            row["path_pattern"],
        )
        summary_counter[key][row["source_tables"]] += 1
    summary = [
        {
            "role_pair": role_pair,
            "decision_status": status,
            "conflict_fields": conflict_fields,
            "path_pattern": path_pattern,
            "source_tables": source_tables,
            "count": count,
        }
        for (role_pair, status, conflict_fields, path_pattern), counts in summary_counter.items()
        for source_tables, count in counts.items()
    ]
    summary.sort(key=lambda item: (-int(item["count"]), item["role_pair"], item["decision_status"]))
    return flat, summary


def orphan_rows(engine, export_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = fetch_rows(
        engine,
        """
        SELECT node_id, object_type, orphan_class, decision_status, decision_reason,
               child_count, relationship_count, evidence
        FROM migration_orphan_classification
        WHERE export_id = :export_id
        ORDER BY orphan_class, object_type, node_id
        """,
        export_id,
    )
    flat: list[dict[str, Any]] = []
    for row in rows:
        evidence = as_json(row.get("evidence")) or {}
        incoming = [str(value) for value in evidence.get("incoming_context_types") or []]
        outgoing = [str(value) for value in evidence.get("outgoing_context_types") or []]
        path_full = evidence.get("path_full")
        flat.append(
            {
                "node_id": row["node_id"],
                "object_type": row.get("object_type"),
                "orphan_class": row["orphan_class"],
                "decision_status": row["decision_status"],
                "decision_reason": row.get("decision_reason"),
                "child_count": int(row.get("child_count") or 0),
                "relationship_count": int(row.get("relationship_count") or 0),
                "path_full": path_full,
                "path_pattern": classify_path_pattern([path_full] if path_full else []),
                "path_depth": evidence.get("path_depth"),
                "name_label": evidence.get("name_label"),
                "name_tech": evidence.get("name_tech"),
                "incoming_context_types": join_values(incoming),
                "outgoing_context_types": join_values(outgoing),
                "labels": join_values([str(value) for value in evidence.get("labels") or []]),
                "suggested_manual_decision": "",
                "reviewer_notes": "",
            }
        )

    summary_counter = Counter(
        (
            row["object_type"],
            row["orphan_class"],
            row["path_pattern"],
            row["incoming_context_types"],
            row["outgoing_context_types"],
        )
        for row in flat
    )
    summary = [
        {
            "object_type": object_type,
            "orphan_class": orphan_class,
            "path_pattern": path_pattern,
            "incoming_context_types": incoming,
            "outgoing_context_types": outgoing,
            "count": count,
        }
        for (object_type, orphan_class, path_pattern, incoming, outgoing), count in summary_counter.items()
    ]
    summary.sort(key=lambda item: (-int(item["count"]), item["object_type"] or ""))
    return flat, summary


def relationship_rows(engine, export_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = fetch_rows(
        engine,
        """
        SELECT relationship_type, baseline_value, v2_value, delta_value, parity_status,
               decision_status, explanation_class, inverse_relationship_type,
               raw_link_types, decision_reason, required_action, evidence
        FROM migration_relationship_explanation
        WHERE export_id = :export_id
        ORDER BY decision_status DESC, relationship_type
        """,
        export_id,
    )
    flat: list[dict[str, Any]] = []
    typed: list[dict[str, Any]] = []
    for row in rows:
        evidence = as_json(row.get("evidence")) or {}
        source_evidence = evidence.get("staging_source_evidence") or {}
        raw_link_types = [str(value) for value in as_list(row.get("raw_link_types"))]
        item = {
            "relationship_type": row["relationship_type"],
            "decision_status": row["decision_status"],
            "parity_status": row["parity_status"],
            "explanation_class": row["explanation_class"],
            "baseline_value": row.get("baseline_value"),
            "v2_value": row.get("v2_value"),
            "delta_value": row.get("delta_value"),
            "inverse_relationship_type": row.get("inverse_relationship_type"),
            "raw_link_types": join_values(raw_link_types),
            "relationship_family": evidence.get("family"),
            "source_tables": join_values([str(value) for value in source_evidence.get("source_tables") or []]),
            "staging_link_types": join_values([str(value) for value in source_evidence.get("link_types") or []]),
            "staging_row_count": source_evidence.get("row_count"),
            "decision_reason": row.get("decision_reason"),
            "required_action": row.get("required_action"),
            "suggested_manual_decision": "",
            "reviewer_notes": "",
        }
        flat.append(item)
        if row["explanation_class"] == "contract_typed_relationship":
            typed.append(item)
    return flat, typed


def v0_trust_rows() -> list[dict[str, Any]]:
    return [
        {
            "question": "Is v0 relationship total enough to block v2?",
            "current_evidence": "v0 exposes aggregate Relationships count; v2 exposes typed relationship counts.",
            "trust_assessment": "medium",
            "decision_impact": "Do not accept/reject total parity without baseline edge/type breakdown.",
            "next_check": "Export v0 relationship edges or type counts and compare to v2 by type.",
        },
        {
            "question": "Is v0 HAS_FIELD count trustworthy?",
            "current_evidence": "Only one edge difference: v0 2082955 vs v2 2082954.",
            "trust_assessment": "high but needs edge identity",
            "decision_impact": "Likely closeable; identify exact missing edge before publish.",
            "next_check": "Run edge-level diff for HAS_FIELD source/target pairs.",
        },
        {
            "question": "Is v0 IMPLEMENTS count trustworthy?",
            "current_evidence": "155 missing v2 IMPLEMENTS links, while v2 also has IS_IMPLEMENTED_BY inverse links.",
            "trust_assessment": "medium",
            "decision_impact": "Counts alone may mix directionality, baseline artifacts, or mapping exclusions.",
            "next_check": "Run semantic edge diff and classify missing links as repair, excluded, inverse-only, or v0 artifact.",
        },
        {
            "question": "Are v0 untyped links comparable to v2 typed/bidirectional links?",
            "current_evidence": "22 v2-extra relationship types map to raw DataGalaxy link values.",
            "trust_assessment": "low as aggregate-only comparator",
            "decision_impact": "Use v0 as historical reference, not single source of truth, for typed relationship semantics.",
            "next_check": "Compare from raw export contracts and edge pairs, not only v0 graph totals.",
        },
        {
            "question": "Are the 27 rootless orphans evidence against v2 or raw source quality?",
            "current_evidence": "10 have placeholder null paths; all 27 are lineage endpoints and none are parents.",
            "trust_assessment": "unknown",
            "decision_impact": "Needs raw-row inspection before accepting as intentional roots.",
            "next_check": "Trace each orphan node_id back to raw/staging payload and decide repair vs explicit acceptance.",
        },
    ]


def main() -> None:
    args = parse_args()
    engine = engine_from_args(args)
    ensure_tables(
        engine,
        [
            "migration_role_resolution",
            "migration_orphan_classification",
            "migration_relationship_explanation",
        ],
    )
    out_dir = review_dir(args.export_id)

    roles, role_summary = role_rows(engine, args.export_id)
    orphans, orphan_summary = orphan_rows(engine, args.export_id)
    relationships, typed_relationships = relationship_rows(engine, args.export_id)
    role_review = [row for row in roles if row["decision_status"] != "accepted"]
    relationship_review = [row for row in relationships if row["decision_status"] != "accepted"]

    write_csv(
        out_dir / "01_role_all_duplicate_nodes.csv",
        roles,
        [
            "node_id",
            "decision_status",
            "canonical_role",
            "observed_roles",
            "role_pair",
            "conflict_fields",
            "decision_reason",
            "source_tables",
            "parent_node_ids",
            "parent_count",
            "paths",
            "path_count",
            "path_pattern",
            "common_prefix_segments",
            "labels",
            "technical_names",
            "suggested_manual_decision",
            "reviewer_notes",
        ],
    )
    write_csv(out_dir / "02_role_review_required.csv", role_review, list(roles[0].keys()) if roles else [])
    write_csv(
        out_dir / "03_role_pattern_summary.csv",
        role_summary,
        ["role_pair", "decision_status", "conflict_fields", "path_pattern", "source_tables", "count"],
    )
    write_csv(
        out_dir / "04_orphan_review_required.csv",
        orphans,
        [
            "node_id",
            "object_type",
            "orphan_class",
            "decision_status",
            "decision_reason",
            "child_count",
            "relationship_count",
            "path_full",
            "path_pattern",
            "path_depth",
            "name_label",
            "name_tech",
            "incoming_context_types",
            "outgoing_context_types",
            "labels",
            "suggested_manual_decision",
            "reviewer_notes",
        ],
    )
    write_csv(
        out_dir / "05_orphan_pattern_summary.csv",
        orphan_summary,
        [
            "object_type",
            "orphan_class",
            "path_pattern",
            "incoming_context_types",
            "outgoing_context_types",
            "count",
        ],
    )
    write_csv(
        out_dir / "06_relationship_review_required.csv",
        relationship_review,
        [
            "relationship_type",
            "decision_status",
            "parity_status",
            "explanation_class",
            "baseline_value",
            "v2_value",
            "delta_value",
            "inverse_relationship_type",
            "raw_link_types",
            "relationship_family",
            "source_tables",
            "staging_link_types",
            "staging_row_count",
            "decision_reason",
            "required_action",
            "suggested_manual_decision",
            "reviewer_notes",
        ],
    )
    write_csv(
        out_dir / "07_relationship_typed_v2_links.csv",
        typed_relationships,
        [
            "relationship_type",
            "decision_status",
            "parity_status",
            "explanation_class",
            "baseline_value",
            "v2_value",
            "delta_value",
            "inverse_relationship_type",
            "raw_link_types",
            "relationship_family",
            "source_tables",
            "staging_link_types",
            "staging_row_count",
            "decision_reason",
        ],
    )
    write_csv(
        out_dir / "08_v0_trustworthiness_assessment.csv",
        v0_trust_rows(),
        ["question", "current_evidence", "trust_assessment", "decision_impact", "next_check"],
    )

    index_rows = [
        ("01_role_all_duplicate_nodes.csv", len(roles), "All duplicate-role node decisions, including accepted rows."),
        ("02_role_review_required.csv", len(role_review), "The 93 rows that need manual role/path acceptance or repair."),
        ("03_role_pattern_summary.csv", len(role_summary), "Grouped duplicate-role patterns."),
        ("04_orphan_review_required.csv", len(orphans), "The 27 rootless Field/UsageField nodes."),
        ("05_orphan_pattern_summary.csv", len(orphan_summary), "Grouped orphan patterns."),
        ("06_relationship_review_required.csv", len(relationship_review), "Blocked/review relationship parity rows."),
        ("07_relationship_typed_v2_links.csv", len(typed_relationships), "Accepted v2 typed relationship mappings."),
        ("08_v0_trustworthiness_assessment.csv", 5, "Questions to decide how much v0 should be trusted."),
    ]
    readme = "\n".join(
        [
            "# Manual Review CSV Index",
            "",
            f"Export: `{args.export_id}`",
            "",
            "| File | Rows | Purpose |",
            "| --- | ---: | --- |",
            *[f"| `{name}` | {count} | {purpose} |" for name, count, purpose in index_rows],
            "",
            "Use `suggested_manual_decision` and `reviewer_notes` columns for review outcomes.",
        ]
    )
    (out_dir / "README.md").write_text(readme, encoding="utf-8")
    write_markdown_report(
        args.export_id,
        "manual_review_csv_index.md",
        "Migration V2 Manual Review CSV Index",
        [("CSV Files", readme)],
    )
    LOGGER.info("Wrote manual review CSVs under %s", out_dir)


if __name__ == "__main__":
    main()
