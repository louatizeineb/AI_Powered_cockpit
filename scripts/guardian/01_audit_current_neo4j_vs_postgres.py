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
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5000"))

OUTPUT_DIR = Path(
    os.getenv(
        "AUDIT_OUTPUT_DIR",
        "reports/migration_guardian/fast_node_presence_audit",
    )
)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


CATALOG_TABLES = [
    ("source", "Source"),
    ("container", "Container"),
    ("structure", "Structure"),
    ("field", "Field"),
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


def ensure_constraints() -> None:
    print("[NEO4J] Ensuring constraints/indexes...")

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

    for query in queries:
        run_neo4j(query)


def get_postgres_count(table: str) -> int:
    row = fetch_pg(f"SELECT COUNT(*) AS count FROM {table} WHERE node_id IS NOT NULL")[0]
    return int(row["count"])


def get_neo4j_label_count(label: str) -> int:
    row = run_neo4j(f"MATCH (n:{label}) WHERE n.node_id IS NOT NULL RETURN count(n) AS count")[0]
    return int(row["count"])


def audit_one_table(table: str, label: str) -> dict[str, Any]:
    print("\n" + "=" * 80)
    print(f"[AUDIT] {table} -> :{label}")
    print("=" * 80)

    output_file = OUTPUT_DIR / f"missing_or_wrong_{table}.csv"

    query_pg = f"""
        SELECT
            node_id,
            {optional_column(table, "name_label")},
            {optional_column(table, "name_tech")},
            {optional_column(table, "path_full")}
        FROM {table}
        WHERE node_id IS NOT NULL
    """

    # Important: label-specific lookup.
    # This uses the :Source/:Container/:Structure/:Field node_id constraint.
    query_neo4j = f"""
    UNWIND $rows AS row
    OPTIONAL MATCH (n:{label} {{node_id: row.node_id}})
    RETURN
        row.node_id AS node_id,
        row.name_label AS postgres_name_label,
        row.name_tech AS postgres_name_tech,
        row.path_full AS postgres_path_full,
        n IS NOT NULL AS exists_in_neo4j,
        labels(n) AS neo4j_labels,
        n.name_label AS neo4j_name_label,
        n.name_tech AS neo4j_name_tech,
        n.path_full AS neo4j_path_full
    """

    total_pg = get_postgres_count(table)
    total_neo4j = get_neo4j_label_count(label)

    checked = 0
    missing = 0

    with output_file.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "node_id",
            "postgres_name_label",
            "postgres_name_tech",
            "postgres_path_full",
            "exists_in_neo4j",
            "neo4j_labels",
            "neo4j_name_label",
            "neo4j_name_tech",
            "neo4j_path_full",
            "status",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for batch_index, batch in enumerate(chunks(stream_pg(query_pg), BATCH_SIZE), start=1):
            rows = run_neo4j(query_neo4j, {"rows": batch})

            for row in rows:
                checked += 1

                if not row["exists_in_neo4j"]:
                    missing += 1
                    row["neo4j_labels"] = "|".join(row.get("neo4j_labels") or [])
                    row["status"] = "MISSING_IN_NEO4J"
                    writer.writerow(row)

            print(
                f"  batch {batch_index:<5} checked={checked:,}/{total_pg:,} "
                f"missing={missing:,}",
                flush=True,
            )

    summary = {
        "table": table,
        "label": label,
        "postgres_count": total_pg,
        "neo4j_label_count": total_neo4j,
        "checked": checked,
        "missing_in_neo4j": missing,
        "output_file": str(output_file),
        "status": "OK" if missing == 0 and total_pg == total_neo4j else "MISMATCH",
    }

    print(f"[DONE] {table} -> :{label}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    return summary


def main() -> None:
    print("=" * 80)
    print("FAST NODE PRESENCE AUDIT")
    print("=" * 80)
    print(f"PostgreSQL: {POSTGRES_URL}")
    print(f"Neo4j:      {NEO4J_URI}")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Output:     {OUTPUT_DIR}")
    print("=" * 80)

    ensure_constraints()

    available_tables = existing_pg_tables()
    summaries = []

    for logical_table, label in CATALOG_TABLES:
        table = resolve_table(logical_table, available_tables)

        if table is None:
            print(f"[WARN] Table not found: {logical_table}")
            continue

        summaries.append(audit_one_table(table, label))

    final_summary = {
        "summaries": summaries,
        "output_dir": str(OUTPUT_DIR),
    }

    summary_path = OUTPUT_DIR / "fast_node_presence_summary.json"
    summary_path.write_text(
        json.dumps(final_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 80)
    print("FAST NODE PRESENCE AUDIT COMPLETE")
    print("=" * 80)
    print(json.dumps(final_summary, indent=2, ensure_ascii=False))

    neo4j.close()


if __name__ == "__main__":
    main()