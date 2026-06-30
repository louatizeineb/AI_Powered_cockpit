from __future__ import annotations

import argparse
import csv
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

DEFAULT_BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1000"))
DEFAULT_OUTPUT_DIR = Path(os.getenv("CATALOG_AUDIT_OUTPUT_DIR", "audit"))

CATALOG_LABELS = ["Source", "Container", "Structure", "Field"]
PARENT_TABLES = [
    ("source", "Source"),
    ("container", "Container"),
    ("structure", "Structure"),
    ("field", "Field"),
]
CHILD_TABLES = [
    ("container", "Container", "CONTAINS"),
    ("structure", "Structure", "CONTAINS"),
    ("field", "Field", "HAS_FIELD"),
]


class CatalogRelationshipAuditor:
    def __init__(
        self,
        postgres_url: str,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        table_prefix: str,
        batch_size: int,
        output_dir: Path,
    ) -> None:
        self.pg = create_engine(postgres_url, pool_pre_ping=True)
        self.neo4j = GraphDatabase.driver(
            neo4j_uri,
            auth=(neo4j_user, neo4j_password),
            connection_timeout=60,
            max_connection_lifetime=3600,
        )
        self.table_prefix = table_prefix
        self.batch_size = batch_size
        self.output_dir = output_dir
        self._tables: set[str] | None = None
        self._columns: dict[str, set[str]] = {}

    def close(self) -> None:
        self.neo4j.close()

    def existing_tables(self) -> set[str]:
        if self._tables is not None:
            return self._tables

        query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        """
        with self.pg.connect() as conn:
            rows = conn.execute(text(query)).mappings().all()

        self._tables = {str(row["table_name"]).lower() for row in rows}
        return self._tables

    def resolve_table(self, logical_name: str) -> str | None:
        tables = self.existing_tables()
        for candidate in self.table_candidates(logical_name):
            if candidate.lower() in tables:
                return candidate
        return None

    def table_columns(self, table_name: str) -> set[str]:
        key = table_name.lower()
        if key in self._columns:
            return self._columns[key]

        query = """
        SELECT column_name
        FROM information_schema.columns
        WHERE lower(table_name) = lower(:table_name)
        """
        with self.pg.connect() as conn:
            rows = conn.execute(text(query), {"table_name": table_name}).mappings().all()

        self._columns[key] = {str(row["column_name"]).lower() for row in rows}
        return self._columns[key]

    def select_expr(
        self,
        table_name: str,
        column_name: str,
        alias: str | None = None,
        default: str = "NULL",
    ) -> str:
        output_name = alias or column_name
        if column_name.lower() in self.table_columns(table_name):
            return f"{column_name} AS {output_name}"
        return f"{default} AS {output_name}"

    def table_candidates(self, logical_name: str) -> list[str]:
        if self.table_prefix == "none":
            return [logical_name]
        if self.table_prefix == "dg_":
            return [f"dg_{logical_name}"]
        return [logical_name, f"dg_{logical_name}"]

    def fetch_all(self, query: str) -> list[dict[str, Any]]:
        with self.pg.connect() as conn:
            result = conn.execute(text(query))
            return [
                {key: clean_value(value) for key, value in row.items()}
                for row in result.mappings().all()
            ]

    def run_cypher(
        self,
        query: str,
        rows: list[dict[str, Any]] | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        with self.neo4j.session() as session:
            result = session.run(query, rows=rows or [], **params)
            records = [dict(row) for row in result]
            result.consume()
            return records

    def run_batches(
        self,
        query: str,
        rows: list[dict[str, Any]],
        **params: Any,
    ) -> int:
        total = len(rows)
        done = 0

        for index, batch in enumerate(chunks(rows, self.batch_size), start=1):
            self.run_cypher(query, batch, **params)
            done += len(batch)
            print(f"    batch {index}: {done}/{total}")

        return done

    def ensure_constraints(self) -> None:
        print("\nEnsuring Neo4j constraints...")

        constraints = [
            "CREATE CONSTRAINT source_id IF NOT EXISTS FOR (n:Source) REQUIRE n.node_id IS UNIQUE",
            "CREATE CONSTRAINT container_id IF NOT EXISTS FOR (n:Container) REQUIRE n.node_id IS UNIQUE",
            "CREATE CONSTRAINT structure_id IF NOT EXISTS FOR (n:Structure) REQUIRE n.node_id IS UNIQUE",
            "CREATE CONSTRAINT field_id IF NOT EXISTS FOR (n:Field) REQUIRE n.node_id IS UNIQUE",
        ]

        for query in constraints:
            self.run_cypher(query)

    def expected_relationship_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        for logical_name, child_label, rel_type in CHILD_TABLES:
            table = self.resolve_table(logical_name)
            if table is None:
                print(f"  skipping expected {child_label} rows: {logical_name} table not found")
                continue

            table_rows = self.fetch_all(f"""
                SELECT
                    {self.select_expr(table, "node_id", "child_node_id")},
                    {self.select_expr(table, "parent_node_id")},
                    {self.select_expr(table, "name_label", "child_name_label")},
                    {self.select_expr(table, "name_tech", "child_name_tech")},
                    {self.select_expr(table, "parent_type")},
                    {self.select_expr(table, "children_count", "child_reported_children_count")}
                FROM {table}
                WHERE node_id IS NOT NULL
                  AND parent_node_id IS NOT NULL
            """)

            for row in table_rows:
                rows.append(
                    {
                        "parent_node_id": row.get("parent_node_id"),
                        "child_node_id": row.get("child_node_id"),
                        "child_label": child_label,
                        "expected_relationship_type": rel_type,
                        "child_name_label": row.get("child_name_label"),
                        "child_name_tech": row.get("child_name_tech"),
                        "postgres_parent_type": row.get("parent_type"),
                        "postgres_child_table": table,
                    }
                )

        return rows

    def postgres_parent_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        for logical_name, label in PARENT_TABLES:
            table = self.resolve_table(logical_name)
            if table is None:
                print(f"  skipping parent table {logical_name}: table not found")
                continue

            table_rows = self.fetch_all(f"""
                SELECT
                    {self.select_expr(table, "node_id")},
                    {self.select_expr(table, "name_label")},
                    {self.select_expr(table, "name_tech")},
                    {self.select_expr(table, "app_code")},
                    {self.select_expr(table, "parent_node_id")},
                    {self.select_expr(table, "parent_type")},
                    {self.select_expr(table, "children_count")}
                FROM {table}
                WHERE node_id IS NOT NULL
            """)

            for row in table_rows:
                rows.append(
                    {
                        "node_id": row.get("node_id"),
                        "node_label": label,
                        "name_label": row.get("name_label"),
                        "name_tech": row.get("name_tech"),
                        "app_code": row.get("app_code"),
                        "parent_node_id": row.get("parent_node_id"),
                        "parent_type": row.get("parent_type"),
                        "postgres_reported_children_count": normalize_int(
                            row.get("children_count")
                        ),
                        "postgres_table": table,
                    }
                )

        return rows

    def postgres_direct_child_counts(
        self,
        expected_rows: list[dict[str, Any]],
    ) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}

        for row in expected_rows:
            parent_id = str(row["parent_node_id"])
            child_label = row["child_label"]

            if parent_id not in counts:
                counts[parent_id] = {
                    "postgres_direct_children_count": 0,
                    "postgres_direct_container_children": 0,
                    "postgres_direct_structure_children": 0,
                    "postgres_direct_field_children": 0,
                }

            counts[parent_id]["postgres_direct_children_count"] += 1
            counts[parent_id][f"postgres_direct_{child_label.lower()}_children"] += 1

        return counts

    def fetch_neo4j_parent_counts(
        self,
        parent_rows: list[dict[str, Any]],
    ) -> dict[str, dict[str, int]]:
        query = """
        UNWIND $rows AS row
        OPTIONAL MATCH (parent {node_id: row.node_id})

        CALL (parent) {
            OPTIONAL MATCH (parent)-[r:CONTAINS|HAS_FIELD]->(child)
            WITH [
                direct_child IN collect(DISTINCT child)
                WHERE direct_child IS NOT NULL
                  AND (
                    direct_child:Source
                    OR direct_child:Container
                    OR direct_child:Structure
                    OR direct_child:Field
                  )
            ] AS direct_children
            RETURN
                size(direct_children) AS direct_children_count,
                size([child IN direct_children WHERE child:Container]) AS direct_container_children,
                size([child IN direct_children WHERE child:Structure]) AS direct_structure_children,
                size([child IN direct_children WHERE child:Field]) AS direct_field_children
        }

        CALL (parent) {
            OPTIONAL MATCH (parent)-[:CONTAINS|HAS_FIELD*1..]->(descendant)
            WITH [
                catalog_descendant IN collect(DISTINCT descendant)
                WHERE catalog_descendant IS NOT NULL
                  AND (
                    catalog_descendant:Container
                    OR catalog_descendant:Structure
                    OR catalog_descendant:Field
                  )
            ] AS descendants
            RETURN
                size(descendants) AS recursive_catalog_descendants,
                size([descendant IN descendants WHERE descendant:Container]) AS recursive_container_descendants,
                size([descendant IN descendants WHERE descendant:Structure]) AS recursive_structure_descendants,
                size([descendant IN descendants WHERE descendant:Field]) AS recursive_field_descendants
        }

        RETURN
            row.node_id AS node_id,
            parent IS NOT NULL AS exists_in_neo4j,
            coalesce(direct_children_count, 0) AS direct_children_count,
            coalesce(direct_container_children, 0) AS direct_container_children,
            coalesce(direct_structure_children, 0) AS direct_structure_children,
            coalesce(direct_field_children, 0) AS direct_field_children,
            coalesce(recursive_catalog_descendants, 0) AS recursive_catalog_descendants,
            coalesce(recursive_container_descendants, 0) AS recursive_container_descendants,
            coalesce(recursive_structure_descendants, 0) AS recursive_structure_descendants,
            coalesce(recursive_field_descendants, 0) AS recursive_field_descendants
        """

        counts: dict[str, dict[str, int]] = {}

        for _, batch in chunks_with_start(parent_rows, self.batch_size):
            records = self.run_cypher(query, batch)
            for record in records:
                counts[str(record["node_id"])] = {
                    "exists_in_neo4j": bool(record["exists_in_neo4j"]),
                    "neo4j_direct_children_count": int(
                        record["direct_children_count"] or 0
                    ),
                    "neo4j_direct_container_children": int(
                        record["direct_container_children"] or 0
                    ),
                    "neo4j_direct_structure_children": int(
                        record["direct_structure_children"] or 0
                    ),
                    "neo4j_direct_field_children": int(
                        record["direct_field_children"] or 0
                    ),
                    "neo4j_recursive_catalog_descendants": int(
                        record["recursive_catalog_descendants"] or 0
                    ),
                    "neo4j_recursive_container_descendants": int(
                        record["recursive_container_descendants"] or 0
                    ),
                    "neo4j_recursive_structure_descendants": int(
                        record["recursive_structure_descendants"] or 0
                    ),
                    "neo4j_recursive_field_descendants": int(
                        record["recursive_field_descendants"] or 0
                    ),
                }

        return counts

    def fetch_relationship_presence(
        self,
        expected_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        query = """
        UNWIND $rows AS row
        OPTIONAL MATCH (parent {node_id: row.parent_node_id})
        OPTIONAL MATCH (child {node_id: row.child_node_id})
        OPTIONAL MATCH (parent)-[any_rel]->(child)
        WITH
            row,
            parent,
            child,
            [rel_type IN collect(DISTINCT type(any_rel)) WHERE rel_type IS NOT NULL] AS relationship_types

        RETURN
            row.parent_node_id AS parent_node_id,
            row.child_node_id AS child_node_id,
            row.child_label AS child_label,
            row.expected_relationship_type AS expected_relationship_type,
            row.child_name_label AS child_name_label,
            row.child_name_tech AS child_name_tech,
            row.postgres_parent_type AS postgres_parent_type,
            row.postgres_child_table AS postgres_child_table,
            parent IS NOT NULL AS parent_exists_in_neo4j,
            child IS NOT NULL AS child_exists_in_neo4j,
            row.expected_relationship_type IN relationship_types AS expected_relationship_exists,
            [rel_type IN relationship_types WHERE rel_type <> row.expected_relationship_type] AS other_relationship_types
        """

        results: list[dict[str, Any]] = []

        for start, batch in chunks_with_start(expected_rows, self.batch_size):
            records = self.run_cypher(query, batch)
            results.extend(records)
            print(
                f"Checked relationship presence "
                f"{min(start + self.batch_size, len(expected_rows))}/{len(expected_rows)}"
            )

        return results

    def fetch_extra_relationships(self) -> list[dict[str, Any]]:
        expected_rows = self.expected_relationship_rows()
        expected_pairs = {
            (
                str(row["parent_node_id"]),
                str(row["child_node_id"]),
                row["expected_relationship_type"],
            )
            for row in expected_rows
        }

        query = """
        MATCH (parent)-[r:CONTAINS|HAS_FIELD]->(child)
        WHERE (parent:Source OR parent:Container OR parent:Structure OR parent:Field)
          AND (child:Container OR child:Structure OR child:Field)
        RETURN
            parent.node_id AS parent_node_id,
            labels(parent) AS parent_labels,
            parent.name_label AS parent_name_label,
            type(r) AS relationship_type,
            child.node_id AS child_node_id,
            labels(child) AS child_labels,
            child.name_label AS child_name_label
        """

        records = self.run_cypher(query)
        extras: list[dict[str, Any]] = []

        for record in records:
            key = (
                str(record["parent_node_id"]),
                str(record["child_node_id"]),
                record["relationship_type"],
            )
            if key in expected_pairs:
                continue

            extras.append(
                {
                    "parent_node_id": record["parent_node_id"],
                    "parent_labels": "|".join(record["parent_labels"] or []),
                    "parent_name_label": record["parent_name_label"],
                    "relationship_type": record["relationship_type"],
                    "child_node_id": record["child_node_id"],
                    "child_labels": "|".join(record["child_labels"] or []),
                    "child_name_label": record["child_name_label"],
                    "status": "EXTRA_IN_NEO4J_NOT_IN_POSTGRES_PARENT_MAP",
                }
            )

        return extras

    def build_parent_audit_rows(
        self,
        parent_rows: list[dict[str, Any]],
        expected_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        postgres_counts = self.postgres_direct_child_counts(expected_rows)
        neo4j_counts = self.fetch_neo4j_parent_counts(parent_rows)

        audit_rows: list[dict[str, Any]] = []

        for parent in parent_rows:
            node_id = str(parent["node_id"])
            pg_count = postgres_counts.get(node_id, empty_postgres_counts())
            n4j_count = neo4j_counts.get(node_id, empty_neo4j_counts())

            reported = parent.get("postgres_reported_children_count")
            pg_direct = pg_count["postgres_direct_children_count"]
            n4j_direct = n4j_count["neo4j_direct_children_count"]
            n4j_recursive = n4j_count["neo4j_recursive_catalog_descendants"]

            audit_rows.append(
                {
                    **parent,
                    **pg_count,
                    **n4j_count,
                    "difference_postgres_direct_minus_neo4j_direct": (
                        pg_direct - n4j_direct
                    ),
                    "difference_reported_minus_postgres_direct": (
                        None if reported is None else reported - pg_direct
                    ),
                    "difference_reported_minus_neo4j_direct": (
                        None if reported is None else reported - n4j_direct
                    ),
                    "difference_reported_minus_neo4j_recursive": (
                        None if reported is None else reported - n4j_recursive
                    ),
                    "status": classify_parent(
                        exists=n4j_count["exists_in_neo4j"],
                        postgres_direct=pg_direct,
                        neo4j_direct=n4j_direct,
                        reported=reported,
                        neo4j_recursive=n4j_recursive,
                    ),
                }
            )

        return audit_rows

    def repair_relationships(self) -> None:
        print("\nRepairing catalog relationships from PostgreSQL parent_node_id...")

        for logical_name, child_label, rel_type in CHILD_TABLES:
            table = self.resolve_table(logical_name)
            if table is None:
                print(f"  skipping {logical_name}: table not found")
                continue

            rows = self.fetch_all(f"""
                SELECT node_id AS child_node_id, parent_node_id
                FROM {table}
                WHERE node_id IS NOT NULL
                  AND parent_node_id IS NOT NULL
            """)

            query = f"""
            UNWIND $rows AS row
            MATCH (child:{child_label} {{node_id: row.child_node_id}})
            MATCH (parent {{node_id: row.parent_node_id}})
            WHERE parent:Source OR parent:Container OR parent:Structure OR parent:Field
            MERGE (parent)-[r:{rel_type}]->(child)
            SET r.imported_from = $imported_from,
                r.source_column = 'parent_node_id',
                r.repaired_at = datetime()
            """

            print(f"\nCreating {rel_type} from {table}: {len(rows)} rows")
            self.run_batches(query, rows, imported_from=table)

    def rebuild_relationships(self) -> None:
        print("\nDeleting existing catalog CONTAINS/HAS_FIELD relationships...")

        query = """
        MATCH (parent)-[r:CONTAINS|HAS_FIELD]->(child)
        WHERE (parent:Source OR parent:Container OR parent:Structure OR parent:Field)
          AND (child:Container OR child:Structure OR child:Field)
        DELETE r
        """
        self.run_cypher(query)

        self.repair_relationships()

    def audit(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        print("\nBuilding expected relationship map from PostgreSQL...")
        expected_rows = self.expected_relationship_rows()
        parent_rows = self.postgres_parent_rows()

        print(f"  expected parent-child relationships: {len(expected_rows)}")
        print(f"  PostgreSQL catalog parent nodes: {len(parent_rows)}")

        print("\nAuditing parent child counts...")
        parent_audit_rows = self.build_parent_audit_rows(parent_rows, expected_rows)

        print("\nAuditing relationship presence...")
        relationship_rows = self.fetch_relationship_presence(expected_rows)

        missing_rows = [
            {
                **row,
                "status": relationship_status(row),
                "other_relationship_types": "|".join(
                    row.get("other_relationship_types") or []
                ),
            }
            for row in relationship_rows
            if not row.get("expected_relationship_exists")
            or not row.get("parent_exists_in_neo4j")
            or not row.get("child_exists_in_neo4j")
        ]

        print("\nFinding extra Neo4j catalog relationships...")
        extra_rows = self.fetch_extra_relationships()

        summary_rows = build_summary(parent_audit_rows, missing_rows, extra_rows)

        parent_output = self.output_dir / "catalog_parent_child_count_audit.csv"
        missing_output = self.output_dir / "catalog_missing_relationships_audit.csv"
        extra_output = self.output_dir / "catalog_extra_relationships_audit.csv"
        summary_output = self.output_dir / "catalog_relationship_audit_summary.csv"

        write_csv(parent_output, parent_audit_rows, PARENT_AUDIT_FIELDS)
        write_csv(missing_output, missing_rows, MISSING_RELATIONSHIP_FIELDS)
        write_csv(extra_output, extra_rows, EXTRA_RELATIONSHIP_FIELDS)
        write_csv(summary_output, summary_rows, SUMMARY_FIELDS)

        print("\nAudit complete")
        print(f"  parent count audit: {parent_output.resolve()}")
        print(f"  missing relationships: {missing_output.resolve()}")
        print(f"  extra relationships: {extra_output.resolve()}")
        print(f"  summary: {summary_output.resolve()}")

        for row in summary_rows:
            print(f"  {row['metric']}: {row['count']}")


PARENT_AUDIT_FIELDS = [
    "node_id",
    "node_label",
    "name_label",
    "name_tech",
    "app_code",
    "parent_node_id",
    "parent_type",
    "postgres_table",
    "exists_in_neo4j",
    "postgres_reported_children_count",
    "postgres_direct_children_count",
    "postgres_direct_container_children",
    "postgres_direct_structure_children",
    "postgres_direct_field_children",
    "neo4j_direct_children_count",
    "neo4j_direct_container_children",
    "neo4j_direct_structure_children",
    "neo4j_direct_field_children",
    "neo4j_recursive_catalog_descendants",
    "neo4j_recursive_container_descendants",
    "neo4j_recursive_structure_descendants",
    "neo4j_recursive_field_descendants",
    "difference_postgres_direct_minus_neo4j_direct",
    "difference_reported_minus_postgres_direct",
    "difference_reported_minus_neo4j_direct",
    "difference_reported_minus_neo4j_recursive",
    "status",
]

MISSING_RELATIONSHIP_FIELDS = [
    "parent_node_id",
    "child_node_id",
    "child_label",
    "expected_relationship_type",
    "child_name_label",
    "child_name_tech",
    "postgres_parent_type",
    "postgres_child_table",
    "parent_exists_in_neo4j",
    "child_exists_in_neo4j",
    "expected_relationship_exists",
    "other_relationship_types",
    "status",
]

EXTRA_RELATIONSHIP_FIELDS = [
    "parent_node_id",
    "parent_labels",
    "parent_name_label",
    "relationship_type",
    "child_node_id",
    "child_labels",
    "child_name_label",
    "status",
]

SUMMARY_FIELDS = ["metric", "count"]


def clean_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def chunks_with_start(
    rows: list[dict[str, Any]],
    size: int,
) -> Iterable[tuple[int, list[dict[str, Any]]]]:
    for index in range(0, len(rows), size):
        yield index, rows[index:index + size]


def normalize_int(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except Exception:
        return None


def empty_postgres_counts() -> dict[str, int]:
    return {
        "postgres_direct_children_count": 0,
        "postgres_direct_container_children": 0,
        "postgres_direct_structure_children": 0,
        "postgres_direct_field_children": 0,
    }


def empty_neo4j_counts() -> dict[str, Any]:
    return {
        "exists_in_neo4j": False,
        "neo4j_direct_children_count": 0,
        "neo4j_direct_container_children": 0,
        "neo4j_direct_structure_children": 0,
        "neo4j_direct_field_children": 0,
        "neo4j_recursive_catalog_descendants": 0,
        "neo4j_recursive_container_descendants": 0,
        "neo4j_recursive_structure_descendants": 0,
        "neo4j_recursive_field_descendants": 0,
    }


def classify_parent(
    exists: bool,
    postgres_direct: int,
    neo4j_direct: int,
    reported: int | None,
    neo4j_recursive: int,
) -> str:
    if not exists:
        return "MISSING_PARENT_NODE_IN_NEO4J"
    if postgres_direct == neo4j_direct:
        if reported is not None and reported == neo4j_recursive:
            return "OK_DIRECT_AND_REPORTED_RECURSIVE"
        return "OK_DIRECT"
    return "DIRECT_CHILD_COUNT_MISMATCH"


def relationship_status(row: dict[str, Any]) -> str:
    if not row.get("parent_exists_in_neo4j"):
        return "MISSING_PARENT_NODE_IN_NEO4J"
    if not row.get("child_exists_in_neo4j"):
        return "MISSING_CHILD_NODE_IN_NEO4J"
    if not row.get("expected_relationship_exists"):
        return "MISSING_EXPECTED_RELATIONSHIP"
    return "OK"


def build_summary(
    parent_rows: list[dict[str, Any]],
    missing_rows: list[dict[str, Any]],
    extra_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    summary: dict[str, int] = {
        "parent_nodes_audited": len(parent_rows),
        "parents_with_direct_count_mismatch": sum(
            1 for row in parent_rows if row["status"] == "DIRECT_CHILD_COUNT_MISMATCH"
        ),
        "parents_missing_in_neo4j": sum(
            1 for row in parent_rows if row["status"] == "MISSING_PARENT_NODE_IN_NEO4J"
        ),
        "missing_expected_relationships": sum(
            1 for row in missing_rows if row["status"] == "MISSING_EXPECTED_RELATIONSHIP"
        ),
        "missing_parent_or_child_nodes": sum(
            1
            for row in missing_rows
            if row["status"]
            in ("MISSING_PARENT_NODE_IN_NEO4J", "MISSING_CHILD_NODE_IN_NEO4J")
        ),
        "extra_neo4j_catalog_relationships": len(extra_rows),
    }

    for row in parent_rows:
        key = f"parent_status_{row['status']}"
        summary[key] = summary.get(key, 0) + 1

    return [{"metric": key, "count": value} for key, value in sorted(summary.items())]


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit and repair Neo4j catalog relationships using PostgreSQL "
            "parent_node_id mappings for Source, Container, Structure, and Field."
        )
    )
    parser.add_argument(
        "--mode",
        choices=["audit", "repair", "audit-repair"],
        default="audit",
        help="audit only, repair only, or repair then audit. Default: audit.",
    )
    parser.add_argument(
        "--table-prefix",
        choices=["auto", "none", "dg_"],
        default=os.getenv("POSTGRES_TABLE_PREFIX", "auto"),
        help="PostgreSQL table naming style. Default: auto.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows per Neo4j batch. Default: {DEFAULT_BATCH_SIZE}.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for audit CSVs. Default: {DEFAULT_OUTPUT_DIR}.",
    )
    parser.add_argument(
        "--rebuild-catalog-relationships",
        action="store_true",
        help=(
            "Before repairing, delete existing CONTAINS/HAS_FIELD relationships "
            "between catalog nodes and rebuild them from PostgreSQL."
        ),
    )
    parser.add_argument(
        "--skip-constraints",
        action="store_true",
        help="Skip Neo4j constraint creation.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    auditor = CatalogRelationshipAuditor(
        postgres_url=POSTGRES_URL,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
        table_prefix=args.table_prefix,
        batch_size=args.batch_size,
        output_dir=args.output_dir,
    )

    try:
        print("Neo4j catalog relationship audit/repair")
        print("=" * 70)
        print(f"Mode: {args.mode}")
        print(f"Table prefix: {args.table_prefix}")
        print(f"Batch size: {args.batch_size}")
        print(f"Output dir: {args.output_dir}")

        if not args.skip_constraints:
            auditor.ensure_constraints()

        if args.mode in ("repair", "audit-repair"):
            if args.rebuild_catalog_relationships:
                auditor.rebuild_relationships()
            else:
                auditor.repair_relationships()

        if args.mode in ("audit", "audit-repair"):
            auditor.audit()

        print("\nDone.")
    finally:
        auditor.close()


if __name__ == "__main__":
    main()
