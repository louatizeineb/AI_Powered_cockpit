from __future__ import annotations

import argparse
import json
from collections import Counter
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


LOGGER = setup_logging("migration_v2.publish_hardening_audit")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate publish-hardening evidence reports for migration_v2."
    )
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    parser.add_argument(
        "--accept-rootless-orphans",
        action="store_true",
        help="Explicitly accept root_without_parent_metadata orphans instead of blocking publish.",
    )
    parser.add_argument(
        "--orphan-rationale",
        default="",
        help="Required human rationale when --accept-rootless-orphans is used.",
    )
    return parser.parse_args()


def load_report(export_id: str, filename: str) -> dict[str, Any] | None:
    path = REPORT_ROOT / export_id / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def status_from_blockers(blockers: list[str]) -> str:
    return "blocked" if blockers else "ready"


def engine_from_args(args: argparse.Namespace):
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
        if v2_config.get("postgres_url"):
            return postgres_engine_from_url(v2_config["postgres_url"])
    return postgres_engine()


def audit_duplicate_nodes(engine, export_id: str) -> dict[str, Any]:
    ensure_tables(engine, ["catalog_object_staging", "migration_validation_finding"])
    with engine.connect() as conn:
        duplicate_rows = conn.execute(
            text(
                """
                WITH grouped AS (
                    SELECT
                        node_id,
                        count(*) AS row_count,
                        array_agg(DISTINCT object_type ORDER BY object_type) AS object_types,
                        array_remove(array_agg(DISTINCT parent_node_id ORDER BY parent_node_id), NULL) AS parent_node_ids,
                        array_remove(array_agg(DISTINCT path_full ORDER BY path_full), NULL) AS paths,
                        array_remove(array_agg(DISTINCT name_label ORDER BY name_label), NULL) AS labels,
                        array_remove(array_agg(DISTINCT name_tech ORDER BY name_tech), NULL) AS technical_names,
                        array_agg(DISTINCT source_table ORDER BY source_table) AS source_tables
                    FROM catalog_object_staging
                    WHERE export_id = :export_id
                    GROUP BY node_id
                    HAVING count(*) > 1
                )
                SELECT *
                FROM grouped
                ORDER BY row_count DESC, node_id
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
        warning_count = int(
            conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM migration_validation_finding
                    WHERE export_id = :export_id
                      AND severity = 'WARN'
                      AND category = 'duplicate_node_id'
                    """
                ),
                {"export_id": export_id},
            ).scalar_one()
        )

    conflicts: list[dict[str, Any]] = []
    role_counts: Counter[str] = Counter()
    for row in duplicate_rows:
        item = dict(row)
        object_types = [str(value) for value in item.get("object_types") or []]
        role_counts[" + ".join(object_types)] += 1
        conflict_fields = [
            field
            for field in ["parent_node_ids", "paths", "labels", "technical_names"]
            if len(item.get(field) or []) > 1
        ]
        if conflict_fields:
            conflicts.append(
                {
                    "node_id": item["node_id"],
                    "row_count": int(item["row_count"]),
                    "object_types": object_types,
                    "conflict_fields": conflict_fields,
                    "parent_node_ids": item.get("parent_node_ids") or [],
                    "paths": item.get("paths") or [],
                    "labels": item.get("labels") or [],
                    "technical_names": item.get("technical_names") or [],
                    "source_tables": item.get("source_tables") or [],
                }
            )

    blockers = []
    if conflicts:
        blockers.append(f"{len(conflicts)} duplicate node_ids have conflicting identity or parent metadata.")

    return {
        "export_id": export_id,
        "status": status_from_blockers(blockers),
        "policy": (
            "Duplicate node_id is accepted only for multi-role DataGalaxy objects "
            "with no conflicting parent_node_id, path_full, name_label, or name_tech."
        ),
        "validation_warning_count": warning_count,
        "duplicate_node_id_count": len(duplicate_rows),
        "conflict_count": len(conflicts),
        "role_pair_counts": dict(role_counts.most_common()),
        "conflict_samples": conflicts[:100],
        "blockers": blockers,
    }


def write_duplicate_node_report(payload: dict[str, Any]) -> tuple[Path, Path]:
    export_id = payload["export_id"]
    json_path = write_json_report(export_id, "duplicate_node_audit_report.json", payload)
    md_path = write_markdown_report(
        export_id,
        "duplicate_node_audit_report.md",
        "Migration V2 Duplicate Node Audit Report",
        [
            ("Status", f"`{payload['status']}`"),
            ("Policy", payload["policy"]),
            (
                "Counts",
                "\n".join(
                    [
                        f"- `validation_warning_count`: {payload['validation_warning_count']}",
                        f"- `duplicate_node_id_count`: {payload['duplicate_node_id_count']}",
                        f"- `conflict_count`: {payload['conflict_count']}",
                    ]
                ),
            ),
            (
                "Role Pairs",
                "\n".join(
                    f"- `{key}`: {value}"
                    for key, value in payload["role_pair_counts"].items()
                )
                or "None.",
            ),
            (
                "Blockers",
                "\n".join(f"- {item}" for item in payload["blockers"]) or "None.",
            ),
        ],
    )
    return json_path, md_path


def audit_orphans(export_id: str, accept_rootless: bool, rationale: str) -> dict[str, Any]:
    graph_audit = load_report(export_id, "graph_audit_report.json")
    blockers: list[str] = []
    if graph_audit is None:
        return {
            "export_id": export_id,
            "status": "blocked",
            "blockers": ["graph_audit_report.json is missing."],
        }

    hierarchy = graph_audit.get("staging_hierarchy") or {}
    neo4j_graph = graph_audit.get("neo4j_graph") or {}
    orphan_classes = neo4j_graph.get("orphan_classification_counts") or {}
    orphan_types = neo4j_graph.get("orphan_counts_by_object_type") or {}
    actionable = int(neo4j_graph.get("actionable_orphan_count") or 0)
    rootless = int(orphan_classes.get("root_without_parent_metadata") or 0)
    irregular = int(hierarchy.get("irregular_allowed_count") or 0)

    accepted_classes = {
        "source_root_expected",
        "usage_root_expected",
        "semantic_term_expected_standalone",
        "processing_context_expected_standalone",
        "non_catalog_context_expected_standalone",
    }
    unexpected_classes = sorted(
        key
        for key, value in orphan_classes.items()
        if key not in accepted_classes and int(value or 0) > 0
    )

    if rootless and not accept_rootless:
        blockers.append(
            f"{rootless} root_without_parent_metadata orphans remain and have not been explicitly accepted."
        )
    if accept_rootless and rootless and not rationale.strip():
        blockers.append("--accept-rootless-orphans requires --orphan-rationale.")
    if any(key not in {"root_without_parent_metadata"} for key in unexpected_classes):
        blockers.append("Unexpected orphan classes remain: " + ", ".join(unexpected_classes))

    return {
        "export_id": export_id,
        "status": status_from_blockers(blockers),
        "policy": (
            "Expected standalone roots are accepted; root_without_parent_metadata "
            "requires repair or explicit human acceptance."
        ),
        "actionable_orphan_count": actionable,
        "root_without_parent_metadata_count": rootless,
        "accepted_rootless_orphans": bool(accept_rootless and rootless and not blockers),
        "orphan_rationale": rationale,
        "orphan_classification_counts": orphan_classes,
        "orphan_counts_by_object_type": orphan_types,
        "irregular_allowed_count": irregular,
        "irregular_policy": (
            "Known DataGalaxy hierarchy patterns, including View->Field and Feature->Usage, "
            "are allowed when graph_auditor classifies them as non-blocking."
        ),
        "blockers": blockers,
    }


def write_orphan_report(payload: dict[str, Any]) -> tuple[Path, Path]:
    export_id = payload["export_id"]
    json_path = write_json_report(export_id, "orphan_resolution_report.json", payload)
    md_path = write_markdown_report(
        export_id,
        "orphan_resolution_report.md",
        "Migration V2 Orphan Resolution Report",
        [
            ("Status", f"`{payload['status']}`"),
            ("Policy", payload.get("policy", "")),
            (
                "Counts",
                "\n".join(
                    [
                        f"- `actionable_orphan_count`: {payload.get('actionable_orphan_count')}",
                        f"- `root_without_parent_metadata_count`: {payload.get('root_without_parent_metadata_count')}",
                        f"- `irregular_allowed_count`: {payload.get('irregular_allowed_count')}",
                    ]
                ),
            ),
            (
                "Orphan Classes",
                "\n".join(
                    f"- `{key}`: {value}"
                    for key, value in (payload.get("orphan_classification_counts") or {}).items()
                )
                or "None.",
            ),
            (
                "Blockers",
                "\n".join(f"- {item}" for item in payload["blockers"]) or "None.",
            ),
        ],
    )
    return json_path, md_path


def audit_relationship_deltas(export_id: str) -> dict[str, Any]:
    parity = load_report(export_id, "relationship_parity_report.json")
    baseline = load_report(export_id, "baseline_report.json")
    graph_audit = load_report(export_id, "graph_audit_report.json")
    blockers: list[str] = []
    explanations: list[dict[str, Any]] = []

    if parity is None:
        return {
            "export_id": export_id,
            "status": "blocked",
            "blockers": ["relationship_parity_report.json is missing."],
            "explanations": [],
        }

    baseline_neo4j = (baseline or {}).get("neo4j_counts") or {}
    v2_relationships = ((graph_audit or {}).get("neo4j_graph") or {}).get("relationship_counts_by_type") or {}
    known_baseline_total = sum(
        int(value or 0)
        for key, value in baseline_neo4j.items()
        if key not in {"DataGalaxyObject", "Source", "Container", "Structure", "Field", "BusinessTerm", "Usage", "Relationships"}
        and key.isupper()
    )
    baseline_relationship_total = baseline_neo4j.get("Relationships")
    untyped_baseline_relationships = None
    if baseline_relationship_total is not None:
        untyped_baseline_relationships = int(baseline_relationship_total) - known_baseline_total

    for row in parity.get("rows") or []:
        metric = row.get("metric_name")
        status = row.get("status")
        explanation_status = "explained"
        reason = ""
        required_action = "none"
        if status == "matched":
            reason = "v0 and v2 counts match."
        elif status == "v2_extra":
            reason = (
                "v2 stores this DataGalaxy link as a typed relationship. "
                "Baseline aggregate counts did not expose this relationship type."
            )
        elif metric == "Relationships" and status == "different":
            reason = (
                "Total relationship counts are not directly comparable because the baseline report "
                f"contains {untyped_baseline_relationships} relationships not broken down by type."
            )
            explanation_status = "needs_baseline_edge_breakdown"
            required_action = "export baseline relationship type counts or accept aggregate-only limitation"
        elif metric == "HAS_FIELD" and status == "different":
            reason = (
                "v2 is missing one HAS_FIELD relationship relative to the aggregate baseline. "
                "The exact edge cannot be identified from the aggregate baseline report alone."
            )
            explanation_status = "blocked"
            required_action = "compare edge-level baseline HAS_FIELD export against v2"
            blockers.append("HAS_FIELD delta remains unresolved.")
        elif metric == "IMPLEMENTS" and status == "different":
            reason = (
                "v2 has fewer semantic IMPLEMENTS links than the aggregate baseline. "
                "This needs edge-level classification as repaired, excluded, or baseline-only."
            )
            explanation_status = "blocked"
            required_action = "compare edge-level baseline IMPLEMENTS export against v2"
            blockers.append("IMPLEMENTS delta remains unresolved.")
        elif status == "missing_in_v2":
            reason = "A baseline relationship type is missing in v2."
            explanation_status = "blocked"
            required_action = "repair or explicitly exclude this relationship type"
            blockers.append(f"{metric} is missing in v2.")
        else:
            reason = "Relationship delta needs human review."
            explanation_status = "review"
            required_action = "review"

        explanations.append(
            {
                **row,
                "explanation_status": explanation_status,
                "reason": reason,
                "required_action": required_action,
            }
        )

    return {
        "export_id": export_id,
        "status": status_from_blockers(blockers),
        "baseline_known_relationship_count": known_baseline_total,
        "baseline_untyped_relationship_count": untyped_baseline_relationships,
        "v2_relationship_counts_by_type": v2_relationships,
        "explanations": explanations,
        "blockers": sorted(set(blockers)),
    }


def write_relationship_report(payload: dict[str, Any]) -> tuple[Path, Path]:
    export_id = payload["export_id"]
    json_path = write_json_report(export_id, "relationship_delta_explanation_report.json", payload)
    md_path = write_markdown_report(
        export_id,
        "relationship_delta_explanation_report.md",
        "Migration V2 Relationship Delta Explanation Report",
        [
            ("Status", f"`{payload['status']}`"),
            (
                "Baseline Coverage",
                "\n".join(
                    [
                        f"- `baseline_known_relationship_count`: {payload.get('baseline_known_relationship_count')}",
                        f"- `baseline_untyped_relationship_count`: {payload.get('baseline_untyped_relationship_count')}",
                    ]
                ),
            ),
            (
                "Explanations",
                "\n".join(
                    f"- `{row['metric_name']}`: status=`{row['status']}` "
                    f"explanation=`{row['explanation_status']}` action=`{row['required_action']}`"
                    for row in payload["explanations"]
                )
                or "None.",
            ),
            (
                "Blockers",
                "\n".join(f"- {item}" for item in payload["blockers"]) or "None.",
            ),
        ],
    )
    return json_path, md_path


def main() -> None:
    args = parse_args()
    engine = engine_from_args(args)

    duplicate_payload = audit_duplicate_nodes(engine, args.export_id)
    duplicate_paths = write_duplicate_node_report(duplicate_payload)

    orphan_payload = audit_orphans(args.export_id, args.accept_rootless_orphans, args.orphan_rationale)
    orphan_paths = write_orphan_report(orphan_payload)

    relationship_payload = audit_relationship_deltas(args.export_id)
    relationship_paths = write_relationship_report(relationship_payload)

    LOGGER.info(
        "Wrote %s, %s, %s, %s, %s and %s",
        *duplicate_paths,
        *orphan_paths,
        *relationship_paths,
    )


if __name__ == "__main__":
    main()
