from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy import create_engine, text


POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql+psycopg2://postgres:louatiza@localhost/DataGalaxy_tables",
)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "bpi_cockpit")

OUTPUT_FILE = Path(os.getenv("OUTPUT_FILE", "source_extended_descendance_count_audit.csv"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))

pg = create_engine(POSTGRES_URL)
neo4j = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    connection_timeout=60,
    max_connection_lifetime=3600,
)


def fetch_sources_from_postgres() -> list[dict[str, Any]]:
    query = """
        SELECT node_id, name_label, name_tech, app_code, children_count
        FROM dg_source
        WHERE node_id IS NOT NULL
    """

    with pg.connect() as conn:
        result = conn.execute(text(query))
        return [dict(row) for row in result.mappings()]


def chunks(items: list[dict[str, Any]], size: int):
    for i in range(0, len(items), size):
        yield i, items[i:i + size]


def normalize_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def fetch_source_extended_counts(batch: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    query = """
    UNWIND $rows AS row
    MATCH (s:Source {node_id: row.node_id})

    CALL {
        WITH s
        OPTIONAL MATCH (s)-[:CONTAINS|HAS_FIELD*1..]->(desc)
        RETURN collect(DISTINCT desc) AS catalog_nodes
    }

    CALL {
        WITH catalog_nodes
        UNWIND CASE WHEN size(catalog_nodes) = 0 THEN [NULL] ELSE catalog_nodes END AS catalog_node
        WITH catalog_node
        WHERE catalog_node IS NOT NULL AND catalog_node:Field
        OPTIONAL MATCH (catalog_node)-[:IMPLEMENTS]->(bt:BusinessTerm)
        RETURN collect(DISTINCT bt) AS business_terms_raw
    }

    CALL {
        WITH s
        OPTIONAL MATCH (u1:Usage)-[:USES]->(s)
        RETURN collect(DISTINCT u1) AS usage_to_source
    }

    CALL {
        WITH catalog_nodes
        UNWIND CASE WHEN size(catalog_nodes) = 0 THEN [NULL] ELSE catalog_nodes END AS catalog_node
        OPTIONAL MATCH (u2:Usage)-[:USES]->(catalog_node)
        RETURN collect(DISTINCT u2) AS usage_to_catalog
    }

    WITH
        row.node_id AS node_id,
        catalog_nodes,
        [x IN business_terms_raw WHERE x IS NOT NULL] AS business_terms,
        [x IN usage_to_source + usage_to_catalog WHERE x IS NOT NULL] AS usage_nodes

    RETURN
        node_id,
        size(catalog_nodes) AS catalog_descendants,
        size([x IN catalog_nodes WHERE x:Container]) AS container_descendants,
        size([x IN catalog_nodes WHERE x:Structure]) AS structure_descendants,
        size([x IN catalog_nodes WHERE x:Field]) AS field_descendants,
        size(business_terms) AS business_term_descendants,
        size(usage_nodes) AS usage_related_nodes,
        size(catalog_nodes) + size(business_terms) + size(usage_nodes) AS extended_related_nodes
    """

    with neo4j.session() as session:
        result = session.run(query, rows=batch)
        records = list(result)

        return {
            r["node_id"]: {
                "catalog_descendants": int(r["catalog_descendants"] or 0),
                "container_descendants": int(r["container_descendants"] or 0),
                "structure_descendants": int(r["structure_descendants"] or 0),
                "field_descendants": int(r["field_descendants"] or 0),
                "business_term_descendants": int(r["business_term_descendants"] or 0),
                "usage_related_nodes": int(r["usage_related_nodes"] or 0),
                "extended_related_nodes": int(r["extended_related_nodes"] or 0),
            }
            for r in records
        }


def classify(reported: int | None, catalog_count: int, extended_count: int) -> str:
    if reported is None:
        return "NO_REPORTED_COUNT"
    if reported == catalog_count:
        return "OK_CATALOG_DESCENDANTS"
    if reported == extended_count:
        return "OK_EXTENDED_WITH_USAGE_AND_LINKS"
    return "MISMATCH"


def main() -> None:
    print("Fetching Source nodes from PostgreSQL...")
    sources = fetch_sources_from_postgres()
    print(f"Loaded {len(sources)} Source nodes")

    audit_rows: list[dict[str, Any]] = []

    for start, batch in chunks(sources, BATCH_SIZE):
        counts = fetch_source_extended_counts(batch)

        for src in batch:
            node_id = src["node_id"]
            reported = normalize_int(src.get("children_count"))

            node_counts = counts.get(
                node_id,
                {
                    "catalog_descendants": 0,
                    "container_descendants": 0,
                    "structure_descendants": 0,
                    "field_descendants": 0,
                    "business_term_descendants": 0,
                    "usage_related_nodes": 0,
                    "extended_related_nodes": 0,
                },
            )

            catalog_count = node_counts["catalog_descendants"]
            extended_count = node_counts["extended_related_nodes"]

            audit_rows.append({
                "node_id": node_id,
                "name_label": src.get("name_label"),
                "name_tech": src.get("name_tech"),
                "app_code": src.get("app_code"),
                "postgres_reported_children_count": reported,
                "neo4j_catalog_descendants": catalog_count,
                "neo4j_container_descendants": node_counts["container_descendants"],
                "neo4j_structure_descendants": node_counts["structure_descendants"],
                "neo4j_field_descendants": node_counts["field_descendants"],
                "neo4j_business_terms_from_links": node_counts["business_term_descendants"],
                "neo4j_usage_related_nodes": node_counts["usage_related_nodes"],
                "neo4j_extended_related_nodes": extended_count,
                "difference_reported_minus_catalog": None if reported is None else reported - catalog_count,
                "difference_reported_minus_extended": None if reported is None else reported - extended_count,
                "status": classify(reported, catalog_count, extended_count),
            })

        print(f"Checked {min(start + BATCH_SIZE, len(sources))}/{len(sources)} sources")

    fieldnames = [
        "node_id",
        "name_label",
        "name_tech",
        "app_code",
        "postgres_reported_children_count",
        "neo4j_catalog_descendants",
        "neo4j_container_descendants",
        "neo4j_structure_descendants",
        "neo4j_field_descendants",
        "neo4j_business_terms_from_links",
        "neo4j_usage_related_nodes",
        "neo4j_extended_related_nodes",
        "difference_reported_minus_catalog",
        "difference_reported_minus_extended",
        "status",
    ]

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(audit_rows)

    summary: dict[str, int] = {}
    for r in audit_rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1

    print("\nAudit complete")
    print(f"Total Source nodes checked: {len(audit_rows)}")
    for status, count in sorted(summary.items()):
        print(f"{status}: {count}")
    print(f"Report saved to: {OUTPUT_FILE.resolve()}")

    neo4j.close()


if __name__ == "__main__":
    main()
