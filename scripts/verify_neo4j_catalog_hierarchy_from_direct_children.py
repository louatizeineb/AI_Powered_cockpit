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

SOURCE_SUMMARY_OUTPUT = Path(
    os.getenv("SOURCE_SUMMARY_OUTPUT", "neo4j_source_catalog_hierarchy_audit.csv")
)

DIRECT_CHILDREN_OUTPUT = Path(
    os.getenv("DIRECT_CHILDREN_OUTPUT", "neo4j_source_direct_children_audit.csv")
)

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))

CATALOG_REL_TYPES = ["CONTAINS", "HAS_FIELD"]

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
        FROM source
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


def classify(postgres_count: int | None, neo4j_count: int) -> str:
    if postgres_count is None:
        return "NO_POSTGRES_REPORTED_COUNT"

    if postgres_count == neo4j_count:
        return "OK"

    return "MISMATCH"


def fetch_source_catalog_audit(
    batch: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    query = """
    UNWIND $rows AS row
    MATCH (s:Source {node_id: row.node_id})

    /*
      1. Identify actual direct children of each Source.
      This does NOT assume that the source must have containers.
      A source may directly contain:
        - Container
        - Structure
        - Field, if such data exists
    */
    CALL {
        WITH s
        OPTIONAL MATCH (s)-[r:CONTAINS|HAS_FIELD]->(direct_child)
        RETURN collect(DISTINCT {
            child_node_id: direct_child.node_id,
            child_name_label: direct_child.name_label,
            child_name_tech: direct_child.name_tech,
            child_labels: labels(direct_child),
            relationship_type: type(r)
        }) AS direct_children_raw,
        collect(DISTINCT direct_child) AS direct_children_nodes
    }

    /*
      2. For each Container, calculate its direct children.
      Usually:
        Container -> Structure
      But this query does not hard-code only Structure.
    */
    CALL {
        WITH s
        OPTIONAL MATCH (s)-[:CONTAINS|HAS_FIELD*0..]->(container:Container)
        WITH DISTINCT container
        WHERE container IS NOT NULL
        OPTIONAL MATCH (container)-[r:CONTAINS|HAS_FIELD]->(child)
        RETURN
            collect(DISTINCT container) AS containers,
            collect(DISTINCT child) AS container_direct_children,
            collect(DISTINCT {
                parent_node_id: container.node_id,
                parent_label: "Container",
                parent_name_label: container.name_label,
                child_node_id: child.node_id,
                child_name_label: child.name_label,
                child_name_tech: child.name_tech,
                child_labels: labels(child),
                relationship_type: type(r)
            }) AS container_child_details_raw
    }

    /*
      3. For each Structure, calculate its direct children.
      Usually:
        Structure -> Field
    */
    CALL {
        WITH s
        OPTIONAL MATCH (s)-[:CONTAINS|HAS_FIELD*0..]->(structure:Structure)
        WITH DISTINCT structure
        WHERE structure IS NOT NULL
        OPTIONAL MATCH (structure)-[r:CONTAINS|HAS_FIELD]->(child)
        RETURN
            collect(DISTINCT structure) AS structures,
            collect(DISTINCT child) AS structure_direct_children,
            collect(DISTINCT {
                parent_node_id: structure.node_id,
                parent_label: "Structure",
                parent_name_label: structure.name_label,
                child_node_id: child.node_id,
                child_name_label: child.name_label,
                child_name_tech: child.name_tech,
                child_labels: labels(child),
                relationship_type: type(r)
            }) AS structure_child_details_raw
    }

    /*
      4. Fields are leaves.
      We count them, but we do not expand from them.
    */
    CALL {
        WITH s
        OPTIONAL MATCH (s)-[:CONTAINS|HAS_FIELD*0..]->(field:Field)
        WITH DISTINCT field
        WHERE field IS NOT NULL
        RETURN collect(DISTINCT field) AS fields
    }

    /*
      5. Count all catalog descendants from the Source, regardless of level.
      This catches:
        Source -> Container -> Structure -> Field
        Source -> Structure -> Field
        Source -> Field
      and any mixed valid hierarchy.
    */
    CALL {
        WITH s
        OPTIONAL MATCH (s)-[:CONTAINS|HAS_FIELD*1..]->(descendant)
        WHERE descendant:Container
           OR descendant:Structure
           OR descendant:Field
        RETURN collect(DISTINCT descendant) AS all_catalog_descendants
    }

    WITH
        row.node_id AS source_node_id,
        direct_children_raw,
        direct_children_nodes,
        containers,
        container_direct_children,
        container_child_details_raw,
        structures,
        structure_direct_children,
        structure_child_details_raw,
        fields,
        all_catalog_descendants

    RETURN
        source_node_id,

        [x IN direct_children_raw WHERE x.child_node_id IS NOT NULL] AS source_direct_children,
        size([x IN direct_children_raw WHERE x.child_node_id IS NOT NULL]) AS source_direct_children_count,

        size([x IN direct_children_nodes WHERE x:Container]) AS source_direct_container_children,
        size([x IN direct_children_nodes WHERE x:Structure]) AS source_direct_structure_children,
        size([x IN direct_children_nodes WHERE x:Field]) AS source_direct_field_children,

        size(containers) AS total_containers_under_source,
        size(container_direct_children) AS total_container_direct_children,

        size(structures) AS total_structures_under_source,
        size(structure_direct_children) AS total_structure_direct_children,

        size(fields) AS total_fields_under_source,

        size([x IN all_catalog_descendants WHERE x:Container]) AS recursive_container_descendants,
        size([x IN all_catalog_descendants WHERE x:Structure]) AS recursive_structure_descendants,
        size([x IN all_catalog_descendants WHERE x:Field]) AS recursive_field_descendants,
        size(all_catalog_descendants) AS recursive_catalog_descendants,

        [x IN container_child_details_raw WHERE x.child_node_id IS NOT NULL] AS container_child_details,
        [x IN structure_child_details_raw WHERE x.child_node_id IS NOT NULL] AS structure_child_details
    """

    source_rows: list[dict[str, Any]] = []
    child_rows: list[dict[str, Any]] = []

    with neo4j.session() as session:
        records = session.run(query, rows=batch)

        for r in records:
            source_node_id = r["source_node_id"]

            source_rows.append(
                {
                    "source_node_id": source_node_id,
                    "source_direct_children_count": int(
                        r["source_direct_children_count"] or 0
                    ),
                    "source_direct_container_children": int(
                        r["source_direct_container_children"] or 0
                    ),
                    "source_direct_structure_children": int(
                        r["source_direct_structure_children"] or 0
                    ),
                    "source_direct_field_children": int(
                        r["source_direct_field_children"] or 0
                    ),
                    "total_containers_under_source": int(
                        r["total_containers_under_source"] or 0
                    ),
                    "total_container_direct_children": int(
                        r["total_container_direct_children"] or 0
                    ),
                    "total_structures_under_source": int(
                        r["total_structures_under_source"] or 0
                    ),
                    "total_structure_direct_children": int(
                        r["total_structure_direct_children"] or 0
                    ),
                    "total_fields_under_source": int(
                        r["total_fields_under_source"] or 0
                    ),
                    "recursive_container_descendants": int(
                        r["recursive_container_descendants"] or 0
                    ),
                    "recursive_structure_descendants": int(
                        r["recursive_structure_descendants"] or 0
                    ),
                    "recursive_field_descendants": int(
                        r["recursive_field_descendants"] or 0
                    ),
                    "recursive_catalog_descendants": int(
                        r["recursive_catalog_descendants"] or 0
                    ),
                }
            )

            for child in r["source_direct_children"]:
                child_rows.append(
                    {
                        "source_node_id": source_node_id,
                        "parent_level": "Source",
                        "parent_node_id": source_node_id,
                        "parent_name_label": None,
                        "relationship_type": child.get("relationship_type"),
                        "child_node_id": child.get("child_node_id"),
                        "child_name_label": child.get("child_name_label"),
                        "child_name_tech": child.get("child_name_tech"),
                        "child_labels": "|".join(child.get("child_labels") or []),
                    }
                )

            for child in r["container_child_details"]:
                child_rows.append(
                    {
                        "source_node_id": source_node_id,
                        "parent_level": "Container",
                        "parent_node_id": child.get("parent_node_id"),
                        "parent_name_label": child.get("parent_name_label"),
                        "relationship_type": child.get("relationship_type"),
                        "child_node_id": child.get("child_node_id"),
                        "child_name_label": child.get("child_name_label"),
                        "child_name_tech": child.get("child_name_tech"),
                        "child_labels": "|".join(child.get("child_labels") or []),
                    }
                )

            for child in r["structure_child_details"]:
                child_rows.append(
                    {
                        "source_node_id": source_node_id,
                        "parent_level": "Structure",
                        "parent_node_id": child.get("parent_node_id"),
                        "parent_name_label": child.get("parent_name_label"),
                        "relationship_type": child.get("relationship_type"),
                        "child_node_id": child.get("child_node_id"),
                        "child_name_label": child.get("child_name_label"),
                        "child_name_tech": child.get("child_name_tech"),
                        "child_labels": "|".join(child.get("child_labels") or []),
                    }
                )

    return source_rows, child_rows


def main() -> None:
    print("Fetching Source nodes from PostgreSQL...")
    sources = fetch_sources_from_postgres()
    print(f"Loaded {len(sources)} Source nodes")

    all_source_audit_rows: list[dict[str, Any]] = []
    all_child_detail_rows: list[dict[str, Any]] = []

    source_by_id = {src["node_id"]: src for src in sources}

    for start, batch in chunks(sources, BATCH_SIZE):
        source_rows, child_rows = fetch_source_catalog_audit(batch)

        for row in source_rows:
            source_id = row["source_node_id"]
            pg_source = source_by_id[source_id]

            postgres_count = normalize_int(pg_source.get("children_count"))
            neo4j_count = row["recursive_catalog_descendants"]

            all_source_audit_rows.append(
                {
                    "source_node_id": source_id,
                    "source_name_label": pg_source.get("name_label"),
                    "source_name_tech": pg_source.get("name_tech"),
                    "app_code": pg_source.get("app_code"),
                    "postgres_reported_children_count": postgres_count,

                    "neo4j_source_direct_children_count": row[
                        "source_direct_children_count"
                    ],
                    "neo4j_source_direct_container_children": row[
                        "source_direct_container_children"
                    ],
                    "neo4j_source_direct_structure_children": row[
                        "source_direct_structure_children"
                    ],
                    "neo4j_source_direct_field_children": row[
                        "source_direct_field_children"
                    ],

                    "neo4j_total_containers_under_source": row[
                        "total_containers_under_source"
                    ],
                    "neo4j_total_container_direct_children": row[
                        "total_container_direct_children"
                    ],

                    "neo4j_total_structures_under_source": row[
                        "total_structures_under_source"
                    ],
                    "neo4j_total_structure_direct_children": row[
                        "total_structure_direct_children"
                    ],

                    "neo4j_total_fields_under_source": row[
                        "total_fields_under_source"
                    ],

                    "neo4j_recursive_container_descendants": row[
                        "recursive_container_descendants"
                    ],
                    "neo4j_recursive_structure_descendants": row[
                        "recursive_structure_descendants"
                    ],
                    "neo4j_recursive_field_descendants": row[
                        "recursive_field_descendants"
                    ],
                    "neo4j_recursive_catalog_descendants": neo4j_count,

                    "difference_postgres_minus_neo4j": (
                        None
                        if postgres_count is None
                        else postgres_count - neo4j_count
                    ),

                    "status": classify(postgres_count, neo4j_count),
                }
            )

        all_child_detail_rows.extend(child_rows)

        print(f"Checked {min(start + BATCH_SIZE, len(sources))}/{len(sources)} sources")

    source_fieldnames = [
        "source_node_id",
        "source_name_label",
        "source_name_tech",
        "app_code",
        "postgres_reported_children_count",

        "neo4j_source_direct_children_count",
        "neo4j_source_direct_container_children",
        "neo4j_source_direct_structure_children",
        "neo4j_source_direct_field_children",

        "neo4j_total_containers_under_source",
        "neo4j_total_container_direct_children",

        "neo4j_total_structures_under_source",
        "neo4j_total_structure_direct_children",

        "neo4j_total_fields_under_source",

        "neo4j_recursive_container_descendants",
        "neo4j_recursive_structure_descendants",
        "neo4j_recursive_field_descendants",
        "neo4j_recursive_catalog_descendants",

        "difference_postgres_minus_neo4j",
        "status",
    ]

    child_fieldnames = [
        "source_node_id",
        "parent_level",
        "parent_node_id",
        "parent_name_label",
        "relationship_type",
        "child_node_id",
        "child_name_label",
        "child_name_tech",
        "child_labels",
    ]

    with SOURCE_SUMMARY_OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=source_fieldnames)
        writer.writeheader()
        writer.writerows(all_source_audit_rows)

    with DIRECT_CHILDREN_OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=child_fieldnames)
        writer.writeheader()
        writer.writerows(all_child_detail_rows)

    summary: dict[str, int] = {}
    for row in all_source_audit_rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1

    print("\nAudit complete")
    print(f"Total Source nodes checked: {len(all_source_audit_rows)}")

    for status, count in sorted(summary.items()):
        print(f"{status}: {count}")

    print(f"\nSource summary report saved to:")
    print(SOURCE_SUMMARY_OUTPUT.resolve())

    print(f"\nDirect children detail report saved to:")
    print(DIRECT_CHILDREN_OUTPUT.resolve())

    neo4j.close()


if __name__ == "__main__":
    main()