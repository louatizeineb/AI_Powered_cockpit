from __future__ import annotations

import argparse
from typing import Any

from sqlalchemy import text

from _common import ensure_tables, json_param, load_contract, postgres_engine, setup_logging, write_json_report, write_markdown_report


LOGGER = setup_logging("migration_v2.validate_staging")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate canonical migration_v2 staging before graph build.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument(
        "--contract",
        default=None,
        help="Optional contract path. Defaults to backend/app/migration_v2/contracts/datagalaxy_athena_v1.yaml.",
    )
    return parser.parse_args()


def finding(severity: str, category: str, message: str, **evidence: Any) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "entity_type": evidence.pop("entity_type", None),
        "node_id": evidence.pop("node_id", None),
        "relationship_id": evidence.pop("relationship_id", None),
        "message": message,
        "evidence": evidence,
    }


def main() -> None:
    args = parse_args()
    contract = load_contract(args.contract) if args.contract else load_contract()
    forbidden_join_columns = set(contract["global_rules"].get("forbidden_join_columns") or [])
    engine = postgres_engine()
    ensure_tables(
        engine,
        [
            "catalog_object_staging",
            "catalog_relationship_staging",
            "migration_validation_finding",
            "migration_mapping_decision",
        ],
    )

    findings: list[dict[str, Any]] = []
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM migration_validation_finding WHERE export_id = :export_id"),
            {"export_id": args.export_id},
        )

        duplicate_nodes = conn.execute(
            text(
                """
                SELECT node_id, count(*) AS count, jsonb_agg(object_type ORDER BY object_type) AS object_types
                FROM catalog_object_staging
                WHERE export_id = :export_id
                GROUP BY node_id
                HAVING count(*) > 1
                ORDER BY count DESC, node_id
                LIMIT 100
                """
            ),
            {"export_id": args.export_id},
        ).mappings().all()
        for row in duplicate_nodes:
            findings.append(
                finding(
                    "WARN",
                    "duplicate_node_id",
                    "The same node_id appears in multiple staged object rows.",
                    node_id=row["node_id"],
                    count=row["count"],
                    object_types=row["object_types"],
                )
            )

        unresolved_parents = conn.execute(
            text(
                """
                SELECT child.node_id, child.parent_node_id, child.object_type
                FROM catalog_object_staging child
                LEFT JOIN catalog_object_staging parent
                  ON parent.export_id = child.export_id
                 AND parent.node_id = child.parent_node_id
                WHERE child.export_id = :export_id
                  AND child.parent_node_id IS NOT NULL
                  AND parent.node_id IS NULL
                LIMIT 500
                """
            ),
            {"export_id": args.export_id},
        ).mappings().all()
        for row in unresolved_parents:
            findings.append(
                finding(
                    "ERROR",
                    "unresolved_parent",
                    "A staged object references a parent_node_id that is not present in staging.",
                    node_id=row["node_id"],
                    parent_node_id=row["parent_node_id"],
                    entity_type=row["object_type"],
                )
            )

        missing_endpoints = conn.execute(
            text(
                """
                SELECT rel.id, rel.src_node_id, rel.tgt_node_id, rel.relationship_type,
                       src.node_id AS src_found, tgt.node_id AS tgt_found
                FROM catalog_relationship_staging rel
                LEFT JOIN catalog_object_staging src
                  ON src.export_id = rel.export_id AND src.node_id = rel.src_node_id
                LEFT JOIN catalog_object_staging tgt
                  ON tgt.export_id = rel.export_id AND tgt.node_id = rel.tgt_node_id
                WHERE rel.export_id = :export_id
                  AND (src.node_id IS NULL OR tgt.node_id IS NULL)
                LIMIT 500
                """
            ),
            {"export_id": args.export_id},
        ).mappings().all()
        for row in missing_endpoints:
            findings.append(
                finding(
                    "ERROR",
                    "missing_relationship_endpoint",
                    "A staged relationship has a source or target endpoint missing from object staging.",
                    relationship_id=row["id"],
                    src_node_id=row["src_node_id"],
                    tgt_node_id=row["tgt_node_id"],
                    relationship_type=row["relationship_type"],
                    src_found=bool(row["src_found"]),
                    tgt_found=bool(row["tgt_found"]),
                )
            )

        mapping_decisions = conn.execute(
            text(
                """
                SELECT raw_table_name, raw_column_name, canonical_field
                FROM migration_mapping_decision
                WHERE export_id = :export_id
                """
            ),
            {"export_id": args.export_id},
        ).mappings().all()
        forbidden_decisions = [
            row
            for row in mapping_decisions
            if row["raw_column_name"] in forbidden_join_columns
            and row["canonical_field"] in {"node_id", "parent_node_id", "src_node_id", "tgt_node_id"}
        ]
        for row in forbidden_decisions:
            findings.append(
                finding(
                    "ERROR",
                    "forbidden_join_column",
                    "A forbidden workspace column is mapped as an entity join key.",
                    raw_table_name=row["raw_table_name"],
                    raw_column_name=row["raw_column_name"],
                    canonical_field=row["canonical_field"],
                )
            )

        eligible_count = conn.execute(
            text(
                """
                SELECT count(*)
                FROM catalog_object_staging
                WHERE export_id = :export_id AND is_graph_eligible
                """
            ),
            {"export_id": args.export_id},
        ).scalar_one()
        if int(eligible_count) == 0:
            findings.append(
                finding(
                    "ERROR",
                    "no_graph_eligible_objects",
                    "No staged objects are graph eligible. Check status values and mapping contract.",
                )
            )

        status_rows = conn.execute(
            text(
                """
                SELECT coalesce(status, '<null>') AS status, count(*) AS count
                FROM catalog_object_staging
                WHERE export_id = :export_id
                GROUP BY coalesce(status, '<null>')
                ORDER BY count DESC
                """
            ),
            {"export_id": args.export_id},
        ).mappings().all()

        for item in findings:
            conn.execute(
                text(
                    """
                    INSERT INTO migration_validation_finding(
                        export_id, severity, category, entity_type, node_id,
                        relationship_id, message, evidence
                    )
                    VALUES (
                        :export_id, :severity, :category, :entity_type, :node_id,
                        :relationship_id, :message, CAST(:evidence AS jsonb)
                    )
                    """
                ),
                {
                    "export_id": args.export_id,
                    **{key: item.get(key) for key in ["severity", "category", "entity_type", "node_id", "relationship_id", "message"]},
                    "evidence": json_param(item["evidence"]),
                },
            )

    severity_counts: dict[str, int] = {}
    for item in findings:
        severity_counts[item["severity"]] = severity_counts.get(item["severity"], 0) + 1
    gate_blocked = severity_counts.get("ERROR", 0) > 0
    payload = {
        "export_id": args.export_id,
        "gate_recommendation": "blocked" if gate_blocked else "review_or_approve",
        "severity_counts": severity_counts,
        "status_distribution": [dict(row) for row in status_rows],
        "findings": findings,
    }
    json_path = write_json_report(args.export_id, "validation_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "validation_report.md",
        "Migration V2 Validation Report",
        [
            ("Gate Recommendation", "`blocked`" if gate_blocked else "`review_or_approve`"),
            ("Severity Counts", "\n".join(f"- `{key}`: {value}" for key, value in sorted(severity_counts.items())) or "None."),
            (
                "Status Distribution",
                "\n".join(f"- `{row['status']}`: {row['count']}" for row in status_rows) or "None.",
            ),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
