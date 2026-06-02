from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict
from datetime import date, datetime
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


CATALOG_TABLES = [
    ("source", "Source"),
    ("container", "Container"),
    ("structure", "Structure"),
    ("field", "Field"),
]

REL_MAPPING = {
    "IsInputOf": "IS_INPUT_OF",
    "IsOutputOf": "IS_OUTPUT_OF",
    "Uses": "USES",
    "IsUsedBy": "IS_USED_BY",
    "Calls": "CALLS",
    "IsCalledBy": "IS_CALLED_BY",
    "Implements": "IMPLEMENTS",
    "IsImplementedBy": "IS_IMPLEMENTED_BY",
    "Generalizes": "GENERALIZES",
    "Specializes": "SPECIALIZES",
    "IsLinkedTo": "IS_LINKED_TO",
    "Regroups": "REGROUPS",
    "IsPartOfDimension": "IS_PART_OF_DIMENSION",
    "HasForSource": "HAS_FOR_SOURCE",
    "IsSourceOf": "IS_SOURCE_OF",
    "HasForUniverse": "HAS_FOR_UNIVERSE",
    "IsUniverseOf": "IS_UNIVERSE_OF",
    "HasForRecordingSystem": "HAS_FOR_RECORDING_SYSTEM",
    "IsRecordingSystemFor": "IS_RECORDING_SYSTEM_FOR",
    "IsUsageSourceFor": "IS_USAGE_SOURCE_FOR",
    "IsUsageDestinationFor": "IS_USAGE_DESTINATION_FOR",
    "IsUsedForComputationOf": "IS_USED_FOR_COMPUTATION_OF",
    "IsSynonymOf": "IS_SYNONYM_OF",
}


class Importer:
    def __init__(
        self,
        postgres_url: str,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        batch_size: int,
        table_prefix: str,
    ) -> None:
        self.pg = create_engine(postgres_url, pool_pre_ping=True)
        self.neo4j = GraphDatabase.driver(
            neo4j_uri,
            auth=(neo4j_user, neo4j_password),
            connection_timeout=60,
            max_connection_lifetime=3600,
        )
        self.batch_size = batch_size
        self.table_prefix = table_prefix
        self._tables: set[str] | None = None

    def close(self) -> None:
        self.neo4j.close()

    def refresh_lineage_search_read_model(self) -> None:
        print("\nPublishing indexed lineage search documents...")
        with self.pg.begin() as conn:
            available = conn.execute(
                text("SELECT to_regprocedure('refresh_lineage_search_documents()')")
            ).scalar()
            if available is None:
                print("  search read model migration not installed; skipping")
                return
            result = conn.execute(
                text("SELECT * FROM refresh_lineage_search_documents()")
            ).mappings().one()
        print(
            f"  graph version: {result['graph_version']}, "
            f"documents: {result['document_count']}"
        )

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
        candidates = self.table_candidates(logical_name)
        tables = self.existing_tables()

        for candidate in candidates:
            if candidate.lower() in tables:
                return candidate

        return None

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
    ) -> None:
        with self.neo4j.session() as session:
            result = session.run(query, rows=rows or [], **params)
            result.consume()

    def run_batches(
        self,
        query: str,
        rows: list[dict[str, Any]],
        **params: Any,
    ) -> int:
        total = len(rows)
        if total == 0:
            return 0

        done = 0
        for index, batch in enumerate(chunks(rows, self.batch_size), start=1):
            self.run_cypher(query, batch, **params)
            done += len(batch)
            print(f"    batch {index}: {done}/{total}")

        return done

    def ensure_constraints(self) -> None:
        print("\nEnsuring Neo4j constraints...")

        constraints = [
            """
            CREATE CONSTRAINT dg_object_node_id IF NOT EXISTS
            FOR (n:DataGalaxyObject)
            REQUIRE n.node_id IS UNIQUE
            """,
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
            CREATE CONSTRAINT usage_uuid IF NOT EXISTS
            FOR (n:Usage)
            REQUIRE n.usage_uuid IS UNIQUE
            """,
            """
            CREATE CONSTRAINT business_term_node_id IF NOT EXISTS
            FOR (n:BusinessTerm)
            REQUIRE n.node_id IS UNIQUE
            """,
            """
            CREATE CONSTRAINT business_term_id IF NOT EXISTS
            FOR (n:BusinessTerm)
            REQUIRE n.term_id IS UNIQUE
            """,
            """
            CREATE CONSTRAINT data_processing_node_id IF NOT EXISTS
            FOR (n:DataProcessing)
            REQUIRE n.node_id IS UNIQUE
            """,
            """
            CREATE CONSTRAINT data_processing_item_node_id IF NOT EXISTS
            FOR (n:DataProcessingItem)
            REQUIRE n.node_id IS UNIQUE
            """,
            """
            CREATE INDEX dg_object_path_full IF NOT EXISTS
            FOR (n:DataGalaxyObject)
            ON (n.path_full)
            """,
            """
            CREATE INDEX dg_object_name_tech IF NOT EXISTS
            FOR (n:DataGalaxyObject)
            ON (n.name_tech)
            """,
            """
            CREATE INDEX is_input_of_link_type IF NOT EXISTS
            FOR ()-[r:IS_INPUT_OF]-()
            ON (r.link_type)
            """,
            """
            CREATE INDEX is_output_of_link_type IF NOT EXISTS
            FOR ()-[r:IS_OUTPUT_OF]-()
            ON (r.link_type)
            """,
        ]

        for query in constraints:
            self.run_cypher(query)

    def load_catalog_nodes(self) -> None:
        for logical_name, label in CATALOG_TABLES:
            table = self.resolve_table(logical_name)
            if table is None:
                print(f"\nSkipping {logical_name}: table not found")
                continue

            print(f"\nLoading {label} nodes from {table}...")
            rows = self.fetch_all(f"""
                SELECT *
                FROM {table}
                WHERE node_id IS NOT NULL
            """)

            query = f"""
            UNWIND $rows AS row
            MERGE (n:{label} {{node_id: row.node_id}})
            SET n:DataGalaxyObject,
                n += row,
                n.catalog_label = $catalog_label,
                n.imported_from = $imported_from
            """

            print(f"  rows: {len(rows)}")
            self.run_batches(
                query,
                rows,
                catalog_label=label,
                imported_from=table,
            )

    def load_usage_nodes(self) -> None:
        table = self.resolve_table("usage")
        if table is None:
            print("\nSkipping usage nodes: table not found")
            return

        print(f"\nLoading Usage nodes from {table}...")
        rows = self.fetch_all(f"""
            SELECT *
            FROM {table}
            WHERE usage_uuid IS NOT NULL
        """)

        query = """
        UNWIND $rows AS row
        MERGE (u:Usage {usage_uuid: row.usage_uuid})
        SET u += row,
            u.imported_from = $imported_from
        """

        print(f"  rows: {len(rows)}")
        self.run_batches(query, rows, imported_from=table)

    def load_hierarchy_relationships(self) -> None:
        hierarchy = [
            ("container", "CONTAINS"),
            ("structure", "CONTAINS"),
            ("field", "HAS_FIELD"),
        ]

        for logical_name, rel_type in hierarchy:
            table = self.resolve_table(logical_name)
            if table is None:
                print(f"\nSkipping {logical_name} hierarchy: table not found")
                continue

            print(f"\nCreating {rel_type} relationships from {table}.parent_node_id...")
            rows = self.fetch_all(f"""
                SELECT node_id, parent_node_id
                FROM {table}
                WHERE node_id IS NOT NULL
                  AND parent_node_id IS NOT NULL
            """)

            query = f"""
            UNWIND $rows AS row
            MATCH (child:DataGalaxyObject {{node_id: row.node_id}})
            MATCH (parent:DataGalaxyObject {{node_id: row.parent_node_id}})
            MERGE (parent)-[r:{rel_type}]->(child)
            SET r.imported_from = $imported_from,
                r.source_column = 'parent_node_id'
            """

            print(f"  rows: {len(rows)}")
            self.run_batches(query, rows, imported_from=table)

    def load_usage_hierarchy_relationships(self) -> None:
        table = self.resolve_table("usage")
        if table is None:
            print("\nSkipping usage hierarchy: table not found")
            return

        print(f"\nCreating CONTAINS relationships from {table}.parent_uuid...")
        rows = self.fetch_all(f"""
            SELECT usage_uuid, parent_uuid
            FROM {table}
            WHERE usage_uuid IS NOT NULL
              AND parent_uuid IS NOT NULL
              AND usage_uuid <> parent_uuid
        """)

        query = """
        UNWIND $rows AS row
        MATCH (child:Usage {usage_uuid: row.usage_uuid})
        MATCH (parent:Usage {usage_uuid: row.parent_uuid})
        MERGE (parent)-[r:CONTAINS]->(child)
        SET r.imported_from = $imported_from,
            r.source_column = 'parent_uuid'
        """

        print(f"  rows: {len(rows)}")
        self.run_batches(query, rows, imported_from=table)

    def load_link_nodes(self, rows: list[dict[str, Any]], link_table: str) -> None:
        print("\nCreating lineage endpoint nodes...")

        grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

        for row in rows:
            for side in ("src", "tgt"):
                node = link_endpoint(row, side)
                if node is None:
                    continue

                label = normalize_label(
                    node.get("entity_type")
                    or node.get("data_type")
                    or "LineageNode"
                )
                grouped[label][node["node_id"]] = node

        total = sum(len(group) for group in grouped.values())
        print(f"  unique endpoints: {total}")

        for label, nodes_by_id in sorted(grouped.items()):
            node_rows = list(nodes_by_id.values())
            print(f"  label {label}: {len(node_rows)}")

            query = lineage_node_merge_query(label)

            self.run_batches(query, node_rows, imported_from=link_table)

    def load_generic_lineage_relationships(
        self,
        rows: list[dict[str, Any]],
        link_table: str,
    ) -> None:
        print("\nCreating typed lineage relationships from link rows...")

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        skipped = 0

        for row in rows:
            src = row.get("src_node_id")
            tgt = row.get("tgt_node_id")
            if not src or not tgt:
                skipped += 1
                continue

            rel_type = normalize_relationship(row.get("link_type"))
            rel_row = dict(row)
            rel_row["src_node_id"] = str(src)
            rel_row["tgt_node_id"] = str(tgt)
            grouped[rel_type].append(rel_row)

        print(f"  skipped rows without endpoints: {skipped}")

        for rel_type, rel_rows in sorted(grouped.items()):
            print(f"  relationship {rel_type}: {len(rel_rows)}")
            query = f"""
            UNWIND $rows AS row
            MATCH (src:DataGalaxyObject {{node_id: row.src_node_id}})
            MATCH (tgt:DataGalaxyObject {{node_id: row.tgt_node_id}})
            MERGE (src)-[r:{rel_type}]->(tgt)
            SET r.link_type = row.link_type,
                r.workspace_id = row.workspace_id,
                r.export_date = row.export_date,
                r.imported_from = $imported_from
            """

            self.run_batches(query, rel_rows, imported_from=link_table)

    def load_business_term_relationships(
        self,
        rows: list[dict[str, Any]],
        link_table: str,
    ) -> None:
        print("\nCreating canonical Field -> BusinessTerm IMPLEMENTS relationships...")

        term_rows = [
            row
            for row in rows
            if row.get("src_node_id") and row.get("tgt_node_id")
        ]

        query = """
        UNWIND $rows AS row
        MATCH (f:Field:DataGalaxyObject {node_id: row.src_node_id})
        MATCH (bt:BusinessTerm:DataGalaxyObject {node_id: row.tgt_node_id})
        MERGE (f)-[r:IMPLEMENTS]->(bt)
        SET r.link_type = row.link_type,
            r.workspace_id = row.workspace_id,
            r.export_date = row.export_date,
            r.imported_from = $imported_from
        """

        print(f"  rows: {len(term_rows)}")
        self.run_batches(query, term_rows, imported_from=link_table)

    def load_lineage_graph(self) -> None:
        table = self.resolve_table("link")
        if table is None:
            print("\nSkipping lineage graph: link table not found")
            return

        print(f"\nLoading lineage rows from {table}...")
        rows = self.fetch_all(f"""
            SELECT *
            FROM {table}
            WHERE src_node_id IS NOT NULL
              AND tgt_node_id IS NOT NULL
        """)
        print(f"  rows: {len(rows)}")

        self.load_link_nodes(rows, table)
        self.load_generic_lineage_relationships(rows, table)
        self.load_business_term_relationships(rows, table)

    def load_usage_relationships(self) -> None:
        table = self.resolve_table("usage")
        if table is None:
            print("\nSkipping usage relationships: usage table not found")
            return

        print(f"\nCreating Usage -> Source relationships from {table}.app_code...")
        rows = self.fetch_all(f"""
            SELECT usage_uuid, app_code
            FROM {table}
            WHERE usage_uuid IS NOT NULL
              AND app_code IS NOT NULL
        """)

        query = """
        UNWIND $rows AS row
        MATCH (u:Usage {usage_uuid: row.usage_uuid})
        MATCH (s:Source {app_code: row.app_code})
        MERGE (u)-[r:USES]->(s)
        SET r.match_method = 'app_code',
            r.imported_from = $imported_from
        """

        print(f"  rows: {len(rows)}")
        self.run_batches(query, rows, imported_from=table)

        print(f"\nCreating Usage -> Structure relationships from {table}.dataset_ref...")
        rows = self.fetch_all(f"""
            SELECT usage_uuid, dataset_ref
            FROM {table}
            WHERE usage_uuid IS NOT NULL
              AND dataset_ref IS NOT NULL
        """)

        query = """
        UNWIND $rows AS row
        MATCH (u:Usage {usage_uuid: row.usage_uuid})
        MATCH (st:Structure)
        WHERE toLower(coalesce(st.name_label, '')) = toLower(row.dataset_ref)
           OR toLower(coalesce(st.name_tech, '')) = toLower(row.dataset_ref)
           OR coalesce(st.path_full, '') CONTAINS row.dataset_ref
        MERGE (u)-[r:USES]->(st)
        SET r.match_method = 'dataset_ref',
            r.imported_from = $imported_from
        """

        print(f"  rows: {len(rows)}")
        self.run_batches(query, rows, imported_from=table)

    def report_orphans(self) -> None:
        print("\nChecking unresolved catalog parents...")

        query = """
        MATCH (n:DataGalaxyObject)
        WHERE (n:Container OR n:Structure OR n:Field)
          AND n.parent_node_id IS NOT NULL
          AND NOT EXISTS {
              MATCH (p:DataGalaxyObject {node_id: n.parent_node_id})
          }
        RETURN labels(n) AS labels, count(n) AS count
        ORDER BY count DESC
        """

        with self.neo4j.session() as session:
            records = list(session.run(query))

        if not records:
            print("  No unresolved parents.")
            return

        for record in records:
            print(f"  {record['labels']}: {record['count']}")

    def print_stats(self) -> None:
        print("\nNeo4j stats:")

        queries = [
            ("DataGalaxyObject", "MATCH (n:DataGalaxyObject) RETURN count(n) AS c"),
            ("Source", "MATCH (n:Source) RETURN count(n) AS c"),
            ("Container", "MATCH (n:Container) RETURN count(n) AS c"),
            ("Structure", "MATCH (n:Structure) RETURN count(n) AS c"),
            ("Field", "MATCH (n:Field) RETURN count(n) AS c"),
            ("BusinessTerm", "MATCH (n:BusinessTerm) RETURN count(n) AS c"),
            ("Usage", "MATCH (n:Usage) RETURN count(n) AS c"),
            ("Relationships", "MATCH ()-[r]->() RETURN count(r) AS c"),
            ("CONTAINS", "MATCH ()-[r:CONTAINS]->() RETURN count(r) AS c"),
            ("HAS_FIELD", "MATCH ()-[r:HAS_FIELD]->() RETURN count(r) AS c"),
            ("IMPLEMENTS", "MATCH ()-[r:IMPLEMENTS]->() RETURN count(r) AS c"),
            ("USES", "MATCH ()-[r:USES]->() RETURN count(r) AS c"),
        ]

        with self.neo4j.session() as session:
            for label, query in queries:
                count = session.run(query).single()["c"]
                print(f"  {label:<18}: {count}")

        print("\nLineage relationship distribution:")
        query = """
        MATCH (:DataGalaxyObject)-[r]->(:DataGalaxyObject)
        RETURN type(r) AS rel_type, count(r) AS count
        ORDER BY count DESC, rel_type ASC
        LIMIT 50
        """
        with self.neo4j.session() as session:
            for row in session.run(query):
                print(f"  {row['rel_type']:<30}: {row['count']}")


def clean_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def normalize_label(label: Any) -> str:
    if label is None:
        return "LineageNode"

    text = str(label).strip()
    if not text:
        return "LineageNode"

    lowered = text.lower().replace("_", " ")
    known = {
        "business term": "BusinessTerm",
        "businessterm": "BusinessTerm",
        "data processing": "DataProcessing",
        "dataprocessing": "DataProcessing",
        "data processing item": "DataProcessingItem",
        "dataprocessingitem": "DataProcessingItem",
        "source": "Source",
        "container": "Container",
        "structure": "Structure",
        "field": "Field",
        "column": "Field",
        "table": "Structure",
        "topic": "Structure",
    }
    if lowered in known:
        return known[lowered]

    parts = re.split(r"[^a-zA-Z0-9]+", text)
    normalized = "".join(part[:1].upper() + part[1:] for part in parts if part)
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", normalized)

    if not normalized:
        return "LineageNode"
    if normalized[0].isdigit():
        return f"L_{normalized}"
    return normalized


def normalize_relationship(rel_type: Any) -> str:
    if rel_type is None:
        return "RELATED_TO"

    text = str(rel_type).strip()
    if not text:
        return "RELATED_TO"

    mapped = REL_MAPPING.get(text, text)
    normalized = re.sub(r"[^A-Z0-9_]", "_", mapped.upper())
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        return "RELATED_TO"
    if normalized[0].isdigit():
        return f"R_{normalized}"
    return normalized


def link_endpoint(row: dict[str, Any], side: str) -> dict[str, Any] | None:
    node_id = row.get(f"{side}_node_id")
    if not node_id:
        return None

    path = row.get(f"{side}_path")
    path_type = row.get(f"{side}_path_type")

    return {
        "node_id": str(node_id),
        "name_label": row.get(f"{side}_name_label"),
        "name_tech": row.get(f"{side}_name_tech"),
        "entity_type": row.get(f"{side}_entity_type"),
        "data_type": row.get(f"{side}_data_type"),
        "path_full": path,
        "path_type": path_type,
        "lineage_role": "source" if side == "src" else "target",
    }


def lineage_node_merge_query(label: str) -> str:
    set_properties = """
    SET n.name_label = coalesce(row.name_label, n.name_label),
        n.name_tech = coalesce(row.name_tech, n.name_tech),
        n.entity_type = coalesce(row.entity_type, n.entity_type),
        n.data_type = coalesce(row.data_type, n.data_type),
        n.path_full = coalesce(row.path_full, n.path_full),
        n.path_type = coalesce(row.path_type, n.path_type),
        n.lineage_role = coalesce(row.lineage_role, n.lineage_role),
        n.imported_from = $imported_from
    """

    if label == "BusinessTerm":
        return f"""
        UNWIND $rows AS row
        MERGE (n:DataGalaxyObject {{node_id: row.node_id}})
        SET n:BusinessTerm:LineageNode,
            n.term_id = coalesce(n.term_id, row.node_id)
        {set_properties}
        """

    if label == "LineageNode":
        return f"""
        UNWIND $rows AS row
        MERGE (n:DataGalaxyObject {{node_id: row.node_id}})
        SET n:LineageNode
        {set_properties}
        """

    return f"""
    UNWIND $rows AS row
    MERGE (n:DataGalaxyObject {{node_id: row.node_id}})
    SET n:{label}:LineageNode
    {set_properties}
    """


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import the DataGalaxy metadata catalog and lineage graph "
            "from PostgreSQL into Neo4j."
        )
    )
    parser.add_argument(
        "--mode",
        choices=["all", "metadata", "lineage", "usage"],
        default="all",
        help="Which graph parts to import. Default: all.",
    )
    parser.add_argument(
        "--table-prefix",
        choices=["auto", "none", "dg_"],
        default=os.getenv("POSTGRES_TABLE_PREFIX", "auto"),
        help=(
            "PostgreSQL table naming style. auto tries source/link/usage "
            "first, then dg_source/dg_link/dg_usage."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows per Neo4j batch. Default: {DEFAULT_BATCH_SIZE}.",
    )
    parser.add_argument(
        "--skip-constraints",
        action="store_true",
        help="Skip Neo4j constraint creation.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    importer = Importer(
        postgres_url=POSTGRES_URL,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
        batch_size=args.batch_size,
        table_prefix=args.table_prefix,
    )

    try:
        print("PostgreSQL -> Neo4j metadata and lineage import")
        print("=" * 70)
        print(f"Mode: {args.mode}")
        print(f"Table prefix: {args.table_prefix}")
        print(f"Batch size: {args.batch_size}")

        if not args.skip_constraints:
            importer.ensure_constraints()

        if args.mode in ("all", "metadata"):
            importer.load_catalog_nodes()
            importer.load_hierarchy_relationships()

        if args.mode in ("all", "usage"):
            importer.load_usage_nodes()
            importer.load_usage_hierarchy_relationships()
            importer.load_usage_relationships()

        if args.mode in ("all", "lineage"):
            importer.load_lineage_graph()

        importer.refresh_lineage_search_read_model()
        importer.report_orphans()
        importer.print_stats()
        print("\nImport completed successfully.")
    finally:
        importer.close()


if __name__ == "__main__":
    main()
