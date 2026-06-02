from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy import create_engine, text


POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql+psycopg2://postgres:change_me@localhost/DataGalaxy_tables",
)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "change_me")

OUTPUT_FILE = Path(
    os.getenv("OUTPUT_FILE", "source_recursive_descendants_audit.csv")
)

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
        return [dict(row) for row in conn.execute(text(query)).mappings()]


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


def fetch_recursive_counts(batch: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    query = """
    UNWIND $rows AS row
    MATCH (s:Source {node_id: row.node_id})

    /*
      Catalog hierarchy:
      Source -> Container -> Structure -> Field

      Fields are counted as descendants, but we do not expand from Field
      because fields have no catalog descendants.
    */

    CALL {
        WITH s
        OPTIONAL MATCH (s)-[:CONTAINS]->(c:Container)
        RETURN collect(DISTINCT c) AS containers
    }

    CALL {
        WITH containers
        UNWIND CASE WHEN size(containers) = 0 THEN [NULL] ELSE containers END AS c
        OPTIONAL MATCH (c)-[:CONTAINS]->(st:Structure)
        RETURN collect(DISTINCT st) AS structures
    }

    CALL {
        WITH structures
        UNWIND CASE WHEN size(structures) = 0 THEN [NULL] ELSE structures END AS st
        OPTIONAL MATCH (st)-[:HAS_FIELD]->(f:Field)
        RETURN collect(DISTINCT f) AS fields
    }

    /*
      Business terms linked from Fields.
      These are not catalog children, but they are lineage/catalog-related
      elements connected to the leaf level.
    */
    CALL {
        WITH fields
        UNWIND CASE WHEN size(fields) = 0 THEN [NULL] ELSE fields END AS f
        OPTIONAL MATCH (f)-[:IMPLEMENTS]->(bt:BusinessTerm)
        RETURN collect(DISTINCT bt) AS business_terms_raw
    }

    /*
      Usage nodes can point to Source, Container, Structure, or Field.
      Count them once, distinct.
    */
    CALL {
        WITH s, containers, structures, fields
        WITH [s] + containers + structures + fields AS all_catalog_nodes
        UNWIND all_catalog_nodes AS n
        OPTIONAL MATCH (u:Usage)-[:USES]->(n)
        RETURN collect(DISTINCT u) AS usage_nodes_raw
    }

    WITH
        row.node_id AS node_id,
        containers,
        structures,
        fields,
        [x IN business_terms_raw WHERE x IS NOT NULL] AS business_terms,
        [x IN usage_nodes_raw WHERE x IS NOT NULL] AS usage_nodes

    WITH
        node_id,
        size(containers) AS container_count,
        size(structures) AS structure_count,
        size(fields) AS field_count,
        size(business_terms) AS business_term_count,
        size(usage_nodes) AS usage_count

    RETURN
        node_id,

        container_count,
        structure_count,
        field_count,

        /*
          Recursive catalog rollup:
          Source count =
              direct containers
            + descendants of containers
            + descendants of structures
            + fields

          Since fields have no descendants, field_count is terminal.
        */
        container_count + structure_count + field_count
            AS neo4j_recursive_catalog_descendants,

        business_term_count,
        usage_count,

        container_count + structure_count + field_count
        + business_term_count + usage_count
            AS neo4j_recursive_extended_descendants
    """

    with neo4j.session() as session:
        records = session.run(query, rows=batch)

        return {
            r["node_id"]: {
                "container_count": int(r["container_count"] or 0),
                "structure_count": int(r["structure_count"] or 0),
                "field_count": int(r["field_count"] or 0),
                "recursive_catalog_descendants": int(
                    r["neo4j_recursive_catalog_descendants"] or 0
                ),
                "business_term_count": int(r["business_term_count"] or 0),
                "usage_count": int(r["usage_count"] or 0),
                "recursive_extended_descendants": int(
                    r["neo4j_recursive_extended_descendants"] or 0
                ),
            }
            for r in records
        }


def classify(
    postgres_reported: int | None,
    neo4j_catalog_count: int,
    neo4j_extended_count: int,
) -> str:
    if postgres_reported is None:
        return "NO_POSTGRES_REPORTED_COUNT"

    if postgres_reported == neo4j_catalog_count:
        return "OK_CATALOG_RECURSIVE"

    if postgres_reported == neo4j_extended_count:
        return "OK_EXTENDED_RECURSIVE"

    return "MISMATCH"


def main() -> None:
    print("Fetching Source nodes from PostgreSQL...")
    sources = fetch_sources_from_postgres()
    print(f"Loaded {len(sources)} Source nodes")

    audit_rows: list[dict[str, Any]] = []

    for start, batch in chunks(sources, BATCH_SIZE):
        counts = fetch_recursive_counts(batch)

        for src in batch:
            node_id = src["node_id"]
            postgres_reported = normalize_int(src.get("children_count"))

            node_counts = counts.get(
                node_id,
                {
                    "container_count": 0,
                    "structure_count": 0,
                    "field_count": 0,
                    "recursive_catalog_descendants": 0,
                    "business_term_count": 0,
                    "usage_count": 0,
                    "recursive_extended_descendants": 0,
                },
            )

            catalog_count = node_counts["recursive_catalog_descendants"]
            extended_count = node_counts["recursive_extended_descendants"]

            audit_rows.append(
                {
                    "node_id": node_id,
                    "name_label": src.get("name_label"),
                    "name_tech": src.get("name_tech"),
                    "app_code": src.get("app_code"),
                    "postgres_reported_children_count": postgres_reported,

                    "neo4j_container_descendants": node_counts["container_count"],
                    "neo4j_structure_descendants": node_counts["structure_count"],
                    "neo4j_field_descendants": node_counts["field_count"],

                    "neo4j_recursive_catalog_descendants": catalog_count,

                    "neo4j_business_term_descendants": node_counts[
                        "business_term_count"
                    ],
                    "neo4j_usage_related_nodes": node_counts["usage_count"],

                    "neo4j_recursive_extended_descendants": extended_count,

                    "difference_reported_minus_catalog": (
                        None
                        if postgres_reported is None
                        else postgres_reported - catalog_count
                    ),
                    "difference_reported_minus_extended": (
                        None
                        if postgres_reported is None
                        else postgres_reported - extended_count
                    ),

                    "status": classify(
                        postgres_reported,
                        catalog_count,
                        extended_count,
                    ),
                }
            )

        print(f"Checked {min(start + BATCH_SIZE, len(sources))}/{len(sources)} sources")

    fieldnames = [
        "node_id",
        "name_label",
        "name_tech",
        "app_code",
        "postgres_reported_children_count",

        "neo4j_container_descendants",
        "neo4j_structure_descendants",
        "neo4j_field_descendants",

        "neo4j_recursive_catalog_descendants",

        "neo4j_business_term_descendants",
        "neo4j_usage_related_nodes",
        "neo4j_recursive_extended_descendants",

        "difference_reported_minus_catalog",
        "difference_reported_minus_extended",
        "status",
    ]

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(audit_rows)

    summary: dict[str, int] = {}
    for row in audit_rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1

    print("\nAudit complete")
    print(f"Total Source nodes checked: {len(audit_rows)}")

    for status, count in sorted(summary.items()):
        print(f"{status}: {count}")

    print(f"Report saved to: {OUTPUT_FILE.resolve()}")

    neo4j.close()


if __name__ == "__main__":
    main()