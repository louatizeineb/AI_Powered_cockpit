from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from neo4j import GraphDatabase

from app.migration_v2.schema_intelligence.models import SchemaProjection


TABLE_CONSTRAINT = (
    "CREATE CONSTRAINT schema_table_key IF NOT EXISTS "
    "FOR (n:Table) REQUIRE n.table_key IS UNIQUE"
)
COLUMN_CONSTRAINT = (
    "CREATE CONSTRAINT schema_column_key IF NOT EXISTS "
    "FOR (n:Column) REQUIRE n.column_key IS UNIQUE"
)

TABLE_UPSERT = """
UNWIND $rows AS row
MERGE (t:Table {table_key: row.table_key})
ON CREATE SET t.created_at = datetime(),
              t.first_seen_export = row.first_seen_export
SET t.table_name = row.table_name,
    t.canonical_table_name = row.canonical_table_name,
    t.description = row.description,
    t.source_system = row.source_system,
    t.object_type = row.object_type,
    t.relationship_table = row.relationship_table,
    t.required_columns = row.required_columns,
    t.observed_column_count = row.observed_column_count,
    t.expected_column_count = row.expected_column_count,
    t.last_seen_export = row.last_seen_export,
    t.name_variants = reduce(acc = [], value IN coalesce(t.name_variants, []) + row.name_variants |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    t.export_ids = reduce(acc = [], value IN coalesce(t.export_ids, []) + row.export_ids |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    t.contract_versions = reduce(acc = [], value IN coalesce(t.contract_versions, []) + row.contract_versions |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    t.file_paths = reduce(acc = [], value IN coalesce(t.file_paths, []) + row.file_paths |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    t.file_hashes = reduce(acc = [], value IN coalesce(t.file_hashes, []) + row.file_hashes |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    t.updated_at = datetime()
"""

COLUMN_UPSERT = """
UNWIND $rows AS row
MATCH (t:Table {table_key: row.table_key})
MERGE (c:Column {column_key: row.column_key})
ON CREATE SET c.created_at = datetime(),
              c.first_seen_export = row.first_seen_export
SET c.column_name = row.column_name,
    c.canonical_column_name = row.canonical_column_name,
    c.raw_column_name = row.raw_column_name,
    c.description = row.description,
    c.description_source = row.description_source,
    c.source_system = row.source_system,
    c.mapping_decision = row.mapping_decision,
    c.mapping_confidence = row.mapping_confidence,
    c.requires_human_approval = row.requires_human_approval,
    c.required_by_contract = row.required_by_contract,
    c.present_in_latest_export = row.present_in_latest_export,
    c.nullable_in_latest_export = row.nullable_in_latest_export,
    c.null_count = row.null_count,
    c.non_null_count = row.non_null_count,
    c.distinct_count = row.distinct_count,
    c.last_seen_export = row.last_seen_export,
    c.name_variants = reduce(acc = [], value IN coalesce(c.name_variants, []) + row.name_variants |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    c.observed_types = reduce(acc = [], value IN coalesce(c.observed_types, []) + row.observed_types |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    c.rules = reduce(acc = [], value IN coalesce(c.rules, []) + row.rules |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    c.warnings = reduce(acc = [], value IN coalesce(c.warnings, []) + row.warnings |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    c.sample_values = reduce(acc = [], value IN coalesce(c.sample_values, []) + row.sample_values |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    c.export_ids = reduce(acc = [], value IN coalesce(c.export_ids, []) + row.export_ids |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    c.contract_versions = reduce(acc = [], value IN coalesce(c.contract_versions, []) + row.contract_versions |
        CASE WHEN value IN acc THEN acc ELSE acc + value END),
    c.updated_at = datetime()
MERGE (t)-[r:HAS_COLUMN]->(c)
ON CREATE SET r.created_at = datetime(),
              r.first_seen_export = row.first_seen_export
SET r.last_seen_export = row.last_seen_export,
    r.updated_at = datetime()
"""


def batched(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


class SchemaIntelligenceKGWriter:
    """Deterministically writes only Table, Column, and HAS_COLUMN graph elements."""

    def __init__(self, uri: str, user: str, password: str, *, database: str | None = None):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self) -> None:
        self.driver.close()

    def verify_connectivity(self) -> None:
        self.driver.verify_connectivity()

    def ensure_schema(self) -> None:
        with self.driver.session(database=self.database) as session:
            session.run(TABLE_CONSTRAINT).consume()
            session.run(COLUMN_CONSTRAINT).consume()
            session.run("CREATE INDEX schema_table_name IF NOT EXISTS FOR (n:Table) ON (n.table_name)").consume()
            session.run("CREATE INDEX schema_column_name IF NOT EXISTS FOR (n:Column) ON (n.column_name)").consume()

    def write(self, projection: SchemaProjection, *, batch_size: int = 500) -> dict[str, Any]:
        self.ensure_schema()
        table_rows = [table.properties() for table in projection.tables]
        column_rows = [
            {"table_key": column.table_key, **column.properties()}
            for column in projection.columns
        ]
        table_keys = [table.table_key for table in projection.tables]
        with self.driver.session(database=self.database) as session:
            session.run(
                """
                UNWIND $table_keys AS table_key
                MATCH (:Table {table_key: table_key})-[:HAS_COLUMN]->(c:Column)
                SET c.present_in_latest_export = false
                """,
                table_keys=table_keys,
            ).consume()
            for rows in batched(table_rows, batch_size):
                session.run(TABLE_UPSERT, rows=rows).consume()
            for rows in batched(column_rows, batch_size):
                session.run(COLUMN_UPSERT, rows=rows).consume()
        return self.audit()

    def audit(self) -> dict[str, Any]:
        with self.driver.session(database=self.database) as session:
            counts = session.run(
                """
                MATCH (t:Table)
                OPTIONAL MATCH (t)-[r:HAS_COLUMN]->(c:Column)
                RETURN count(DISTINCT t) AS table_count,
                       count(DISTINCT c) AS column_count,
                       count(r) AS has_column_count
                """
            ).single(strict=True)
            invalid_relationship_count = session.run(
                "MATCH ()-[r]->() WHERE type(r) <> 'HAS_COLUMN' RETURN count(r) AS count"
            ).single(strict=True)["count"]
            columns_without_one_table = session.run(
                """
                MATCH (c:Column)
                OPTIONAL MATCH (:Table)-[r:HAS_COLUMN]->(c)
                WITH c, count(r) AS parent_count
                WHERE parent_count <> 1
                RETURN count(c) AS count
                """
            ).single(strict=True)["count"]
            column_outgoing_relationships = session.run(
                "MATCH (c:Column)-[r]->() RETURN count(r) AS count"
            ).single(strict=True)["count"]
            invalid_node_labels = session.run(
                """
                MATCH (n)
                WHERE NOT n:Table AND NOT n:Column
                RETURN count(n) AS count
                """
            ).single(strict=True)["count"]
        payload = dict(counts)
        payload.update(
            {
                "invalid_relationship_count": int(invalid_relationship_count),
                "columns_without_one_table": int(columns_without_one_table),
                "column_outgoing_relationships": int(column_outgoing_relationships),
                "invalid_node_labels": int(invalid_node_labels),
            }
        )
        payload["status"] = (
            "ready"
            if not any(
                payload[key]
                for key in (
                    "invalid_relationship_count",
                    "columns_without_one_table",
                    "column_outgoing_relationships",
                    "invalid_node_labels",
                )
            )
            else "blocked"
        )
        return payload
