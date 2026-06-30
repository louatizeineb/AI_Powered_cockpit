from __future__ import annotations

import argparse
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy import text

from _common import (
    config_section,
    ensure_tables,
    load_contract,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)
from app.migration_v2.schema_intelligence.projector import build_schema_projection
from app.migration_v2.schema_intelligence.writer import SchemaIntelligenceKGWriter


LOGGER = setup_logging("migration_v2.build_schema_intelligence_kg")
REQUIRED_TABLES = [
    "migration_export_run",
    "migration_raw_file",
    "migration_column_profile",
    "migration_mapping_decision",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Table/Column Schema Intelligence KG.")
    parser.add_argument("--export-id", required=True)
    parser.add_argument("--env-config", help="Config containing v2 and schema_intelligence sections.")
    parser.add_argument("--contract", help="Versioned mapping contract path.")
    parser.add_argument("--source-system", default="datagalaxy_athena")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="Build and report the projection without Neo4j writes.")
    return parser.parse_args()


def load_evidence(engine, export_id: str) -> dict[str, Any]:
    ensure_tables(engine, REQUIRED_TABLES)
    with engine.connect() as conn:
        export = conn.execute(
            text(
                """
                SELECT export_id, export_path, contract_version
                FROM migration_export_run
                WHERE export_id = :export_id
                """
            ),
            {"export_id": export_id},
        ).mappings().first()
        if export is None:
            raise SystemExit(f"Export {export_id!r} is not registered.")
        raw_files = conn.execute(
            text(
                """
                SELECT raw_table_name, file_path, file_hash, row_count, column_count
                FROM migration_raw_file
                WHERE export_id = :export_id
                ORDER BY raw_table_name, file_path
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
        profiles = conn.execute(
            text(
                """
                SELECT raw_table_name, column_name, data_type_guess, null_count,
                       distinct_count, non_null_count, sample_values, warnings
                FROM migration_column_profile
                WHERE export_id = :export_id
                ORDER BY raw_table_name, column_name
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
        decisions = conn.execute(
            text(
                """
                SELECT id, raw_table_name, raw_column_name, canonical_field,
                       decision_type, confidence, requires_human_approval,
                       rationale, evidence
                FROM migration_mapping_decision
                WHERE export_id = :export_id
                ORDER BY id
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
    if not profiles:
        raise SystemExit(f"Export {export_id!r} has no column profiles. Run 02_profile_export.py first.")
    return {
        "export": dict(export),
        "raw_files": [dict(row) for row in raw_files],
        "profiles": [dict(row) for row in profiles],
        "decisions": [dict(row) for row in decisions],
    }


def projection_summary(projection) -> dict[str, Any]:
    missing = [column for column in projection.columns if not column.present_in_latest_export]
    review = [column for column in projection.columns if column.requires_human_approval]
    warnings = [column for column in projection.columns if column.warnings]
    return {
        "table_count": len(projection.tables),
        "column_count": len(projection.columns),
        "has_column_count": projection.relationship_count,
        "missing_contract_column_count": len(missing),
        "human_review_column_count": len(review),
        "warning_column_count": len(warnings),
        "missing_contract_columns": [
            {
                "table_key": column.table_key,
                "column_name": column.column_name,
                "raw_column_name": column.raw_column_name,
            }
            for column in missing
        ],
        "human_review_columns": [
            {
                "table_key": column.table_key,
                "column_name": column.column_name,
                "mapping_decision": column.mapping_decision,
            }
            for column in review
        ],
    }


def main() -> None:
    args = parse_args()
    config = load_env_config(args.env_config) if args.env_config else None
    if config:
        v2 = config_section(config, "v2")
        engine = postgres_engine_from_url(v2["postgres_url"])
    else:
        engine = postgres_engine()
    contract = load_contract(args.contract) if args.contract else load_contract()
    evidence = load_evidence(engine, args.export_id)
    projection = build_schema_projection(
        export_id=args.export_id,
        contract=contract,
        profiles=evidence["profiles"],
        mapping_decisions=evidence["decisions"],
        raw_files=evidence["raw_files"],
        source_system=args.source_system,
    )
    summary = projection_summary(projection)
    graph_audit = None
    status = "dry_run_ready" if args.dry_run else "pending"

    if not args.dry_run:
        if not config:
            raise SystemExit("--env-config is required for Schema Intelligence Neo4j writes.")
        graph = config_section(config, "schema_intelligence")
        writer = SchemaIntelligenceKGWriter(
            graph["neo4j_uri"],
            graph["neo4j_user"],
            graph["neo4j_password"],
            database=graph.get("neo4j_database"),
        )
        try:
            writer.verify_connectivity()
            graph_audit = writer.write(projection, batch_size=args.batch_size)
        finally:
            writer.close()
        status = "completed" if graph_audit.get("status") == "ready" else "blocked"

    payload = {
        "export_id": args.export_id,
        "status": status,
        "dry_run": args.dry_run,
        "source_system": projection.source_system,
        "contract_version": projection.contract_version,
        "graph_contract": {
            "node_labels": ["Table", "Column"],
            "relationship_types": ["HAS_COLUMN"],
            "column_outgoing_relationships_allowed": False,
        },
        "projection": summary,
        "graph_audit": graph_audit,
        "table_samples": [table.properties() for table in projection.tables[:10]],
        "column_samples": [column.properties() for column in projection.columns[:20]],
    }
    projection_path = write_json_report(
        args.export_id,
        "schema_intelligence_projection.json",
        projection.model_dump(mode="json"),
    )
    json_path = write_json_report(args.export_id, "schema_intelligence_kg_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "schema_intelligence_kg_report.md",
        "Schema Intelligence KG Build Report",
        [
            ("Status", f"`{status}`"),
            (
                "Graph Contract",
                "- Nodes: `Table`, `Column`\n"
                "- Relationship: `(:Table)-[:HAS_COLUMN]->(:Column)`\n"
                "- Column-to-column and column-to-other-node relationships: forbidden",
            ),
            (
                "Projection",
                "\n".join(
                    [
                        f"- `table_count`: {summary['table_count']}",
                        f"- `column_count`: {summary['column_count']}",
                        f"- `has_column_count`: {summary['has_column_count']}",
                        f"- `missing_contract_column_count`: {summary['missing_contract_column_count']}",
                        f"- `human_review_column_count`: {summary['human_review_column_count']}",
                        f"- `warning_column_count`: {summary['warning_column_count']}",
                    ]
                ),
            ),
            (
                "Graph Audit",
                "Not written (`--dry-run`)." if graph_audit is None else "\n".join(
                    f"- `{key}`: {value}" for key, value in graph_audit.items()
                ),
            ),
        ],
    )
    LOGGER.info("Wrote %s, %s and %s", projection_path, json_path, md_path)
    if status == "blocked":
        raise SystemExit("Schema Intelligence KG audit failed. See schema_intelligence_kg_report.md.")


if __name__ == "__main__":
    main()
