from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from neo4j import GraphDatabase
from sqlalchemy import create_engine, text


POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql+psycopg2://postgres:change_me@localhost/DataGalaxy_tables",
)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "change_me")

POSTGRES_TABLE_PREFIX = os.getenv("POSTGRES_TABLE_PREFIX", "auto")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10000"))

OUTPUT_DIR = Path(
    os.getenv(
        "AUDIT_OUTPUT_DIR",
        "reports/migration_guardian/fast_hierarchy_audit",
    )
)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


CHILD_TABLES = [
    # logical table, child label, expected relationship
    ("container", "Container", "CONTAINS"),
    ("structure", "Structure", "CONTAINS"),
    ("field", "Field", "HAS_FIELD"),
]


pg = create_engine(POSTGRES_URL, pool_pre_ping=True)

neo4j = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    connection_timeout=60,
    max_connection_lifetime=3600,
)


def clean_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def fetch_pg(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with pg.connect() as conn:
        result = conn.execute(text(query), params or {})
        return [
            {key: clean_value(value) for key, value in row.items()}
            for row in result.mappings().all()
        ]


def stream_pg(query: str, params: dict[str, Any] | None = None):
    with pg.connect().execution_options(stream_results=True) as conn:
        result = conn.execute(text(query), params or {})
        for row in result.mappings():
            yield {key: clean_value(value) for key, value in row.items()}


def run_neo4j(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with neo4j.session() as session:
        result = session.run(query, **(params or {}))
        rows = [dict(record) for record in result]
        result.consume()
        return rows


def chunks(iterator: Iterable[dict[str, Any]], size: int):
    batch = []
    for item in iterator:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def existing_pg_tables() -> set[str]:
    rows = fetch_pg(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        """
    )
    return {row["table_name"].lower() for row in rows}


def table_candidates(logical_name: str) -> list[str]:
    if POSTGRES_TABLE_PREFIX == "none":
        return [logical_name]
    if POSTGRES_TABLE_PREFIX == "dg_":
        return [f"dg_{logical_name}"]
    return [logical_name, f"dg_{logical_name}"]


def resolve_table(logical_name: str, available_tables: set[str]) -> str | None:
    for candidate in table_candidates(logical_name):
        if candidate.lower() in available_tables:
            return candidate
    return None


def table_columns(table_name: str) -> set[str]:
    rows = fetch_pg(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE lower(table_name) = lower(:table_name)
        """,
        {"table_name": table_name},
    )
    return {row["column_name"].lower() for row in rows}


def optional_column(table_name: str, column_name: str, alias: str | None = None) -> str:
    output = alias or column_name
    if column_name.lower() in table_columns(table_name):
        return f"{column_name} AS {output}"
    return f"NULL AS {output}"


def ensure_indexes() -> None:
    print("[NEO4J] Ensuring indexes...")

    queries = [
        """
        CREATE CONSTRAINT source_node_id IF NOT EXISTS
        FOR (n:Source)
        REQUIRE n.node_id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT container_node_id IF NOT EXISTS
        FOR (n:Container)
        REQUIRE n.node_id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT structure_node_id IF NOT EXISTS
        FOR (n:Structure)
        REQUIRE n.node_id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT field_node_id IF NOT EXISTS
        FOR (n:Field)
        REQUIRE n.node_id IS UNIQUE
        """,
        """
        CREATE INDEX dg_object_node_id IF NOT EXISTS
        FOR (n:DataGalaxyObject)
        ON (n.node_id)
        """,
    ]

    for q in queries:
        run_neo4j(q)


def count_expected_relationships(table: str) -> int:
    row = fetch_pg(
        f"""
        SELECT COUNT(*) AS count
        FROM {table}
        WHERE node_id IS NOT NULL
          AND parent_node_id IS NOT NULL
        """
    )[0]
    return int(row["count"])


def audit_child_table(table: str, child_label: str, expected_rel: str) -> dict[str, Any]:
    print("\n" + "=" * 80)
    print(f"[AUDIT] {table} parent_node_id -> :{child_label} via :{expected_rel}")
    print("=" * 80)

    output_file = OUTPUT_DIR / f"bad_hierarchy_{table}.csv"

    pg_query = f"""
        SELECT
            node_id AS child_node_id,
            parent_node_id,
            {optional_column(table, "name_label", "child_name_label")},
            {optional_column(table, "name_tech", "child_name_tech")},
            {optional_column(table, "path_full", "child_path_full")},
            {optional_column(table, "parent_type")},
            {optional_column(table, "parent_data_type")}
        FROM {table}
        WHERE node_id IS NOT NULL
          AND parent_node_id IS NOT NULL
    """

    # Important:
    # - child lookup is label-specific and indexed.
    # - parent lookup uses DataGalaxyObject index when available.
    # - only checks the expected relation, not all relationships.
    neo_query = f"""
    UNWIND $rows AS row

    OPTIONAL MATCH (child:{child_label} {{node_id: row.child_node_id}})
    OPTIONAL MATCH (parent:DataGalaxyObject {{node_id: row.parent_node_id}})
    OPTIONAL MATCH (parent)-[rel:{expected_rel}]->(child)

    RETURN
        row.parent_node_id AS parent_node_id,
        row.child_node_id AS child_node_id,
        row.child_name_label AS child_name_label,
        row.child_name_tech AS child_name_tech,
        row.child_path_full AS child_path_full,
        row.parent_type AS postgres_parent_type,
        row.parent_data_type AS postgres_parent_data_type,

        child IS NOT NULL AS child_exists,
        parent IS NOT NULL AS parent_exists,
        rel IS NOT NULL AS relationship_exists,

        labels(parent) AS parent_labels,
        parent.name_label AS parent_name_label,
        parent.name_tech AS parent_name_tech,
        labels(child) AS child_labels
    """

    total_expected = count_expected_relationships(table)
    checked = 0
    missing_parent = 0
    missing_child = 0
    missing_relationship = 0
    ok = 0

    with output_file.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "status",
            "parent_node_id",
            "parent_labels",
            "parent_name_label",
            "parent_name_tech",
            "child_node_id",
            "child_labels",
            "child_name_label",
            "child_name_tech",
            "child_path_full",
            "postgres_parent_type",
            "postgres_parent_data_type",
            "expected_relationship",
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for batch_index, batch in enumerate(chunks(stream_pg(pg_query), BATCH_SIZE), start=1):
            rows = run_neo4j(neo_query, {"rows": batch})

            for row in rows:
                checked += 1

                row["parent_labels"] = "|".join(row.get("parent_labels") or [])
                row["child_labels"] = "|".join(row.get("child_labels") or [])
                row["expected_relationship"] = expected_rel

                if not row["parent_exists"]:
                    row["status"] = "PARENT_MISSING_IN_NEO4J"
                    missing_parent += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                elif not row["child_exists"]:
                    row["status"] = "CHILD_MISSING_IN_NEO4J"
                    missing_child += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                elif not row["relationship_exists"]:
                    row["status"] = "RELATIONSHIP_MISSING"
                    missing_relationship += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                else:
                    ok += 1

            print(
                f"  batch {batch_index:<5} checked={checked:,}/{total_expected:,} "
                f"ok={ok:,} missing_rel={missing_relationship:,} "
                f"missing_parent={missing_parent:,} missing_child={missing_child:,}",
                flush=True,
            )

    summary = {
        "table": table,
        "child_label": child_label,
        "expected_relationship": expected_rel,
        "expected_relationships_from_postgres": total_expected,
        "checked": checked,
        "ok": ok,
        "missing_parent": missing_parent,
        "missing_child": missing_child,
        "missing_relationship": missing_relationship,
        "bad_rows_file": str(output_file),
        "status": "OK"
        if checked == ok and missing_parent == 0 and missing_child == 0 and missing_relationship == 0
        else "MISMATCH",
    }

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main() -> None:
    print("=" * 80)
    print("FAST HIERARCHY RELATIONSHIP AUDIT")
    print("=" * 80)
    print(f"PostgreSQL: {POSTGRES_URL}")
    print(f"Neo4j:      {NEO4J_URI}")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Output:     {OUTPUT_DIR}")
    print("=" * 80)

    ensure_indexes()

    available_tables = existing_pg_tables()
    summaries = []

    for logical_table, child_label, expected_rel in CHILD_TABLES:
        table = resolve_table(logical_table, available_tables)

        if table is None:
            print(f"[WARN] Table not found: {logical_table}")
            continue

        summaries.append(audit_child_table(table, child_label, expected_rel))

    final_summary = {
        "summaries": summaries,
        "output_dir": str(OUTPUT_DIR),
    }

    summary_file = OUTPUT_DIR / "fast_hierarchy_audit_summary.json"
    summary_file.write_text(
        json.dumps(final_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 80)
    print("FAST HIERARCHY RELATIONSHIP AUDIT COMPLETE")
    print("=" * 80)
    print(json.dumps(final_summary, indent=2, ensure_ascii=False))

    neo4j.close()


if __name__ == "__main__":
    main()