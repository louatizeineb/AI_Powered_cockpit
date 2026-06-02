import os
import re
from collections import Counter

from sqlalchemy import create_engine, text
from neo4j import GraphDatabase

# ============================================================
# CONFIG
# ============================================================

POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql+psycopg2://postgres:change_me@localhost/DataGalaxy_tables"
)

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "change_me"

BATCH_SIZE = 5000

pg = create_engine(POSTGRES_URL)

neo4j_driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD)
)

# ============================================================
# RELATIONSHIP MAPPING
# ============================================================

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
    "IsSynonymOf": "IS_SYNONYM_OF"
}

# ============================================================
# HELPERS
# ============================================================

def normalize_label(label):

    if label is None:
        return "Unknown"

    label = str(label).strip()

    if label == "":
        return "Unknown"

    label = re.sub(r"[^a-zA-Z0-9_]", "_", label)

    if label[0].isdigit():
        label = f"L_{label}"

    return label


def normalize_rel(rel):

    if rel is None:
        return "UNKNOWN_REL"

    rel = REL_MAPPING.get(rel, rel)

    rel = str(rel).upper()

    rel = re.sub(r"[^A-Z0-9_]", "_", rel)

    return rel


def fetch(query):

    with pg.connect() as conn:

        result = conn.execute(text(query))

        cols = result.keys()

        return [
            dict(zip(cols, row))
            for row in result.fetchall()
        ]


def chunked(rows, size):

    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def ensure_constraints():

    print("\n=== Ensuring Neo4j constraints and indexes ===")

    queries = [
        "CREATE CONSTRAINT source_node_id IF NOT EXISTS FOR (n:Source) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT structure_node_id IF NOT EXISTS FOR (n:Structure) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT field_node_id IF NOT EXISTS FOR (n:Field) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT data_processing_node_id IF NOT EXISTS FOR (n:DataProcessing) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT data_processing_item_node_id IF NOT EXISTS FOR (n:DataProcessingItem) REQUIRE n.node_id IS UNIQUE",
        "CREATE INDEX data_processing_path IF NOT EXISTS FOR (n:DataProcessing) ON (n.path)",
        "CREATE INDEX data_processing_item_path IF NOT EXISTS FOR (n:DataProcessingItem) ON (n.path)",
        "CREATE INDEX field_name IF NOT EXISTS FOR (n:Field) ON (n.name)",
    ]

    with neo4j_driver.session() as session:
        for query in queries:
            session.run(query).consume()

    print("Constraints and indexes ready")


# ============================================================
# LOAD LINKS FROM POSTGRES
# ============================================================

def load_links():

    print("Loading link from Postgres...")

    rows = fetch("""
        SELECT
            src_node_id,
            tgt_node_id,

            src_name_label,
            tgt_name_label,

            src_entity_type,
            tgt_entity_type,

            link_type,

            tgt_path

        FROM link
    """)

    print(f"Loaded {len(rows):,} rows")

    return rows


# ============================================================
# CREATE NODES
# ============================================================

def create_nodes(rows):

    print("\n=== Creating missing nodes ===")

    grouped = {}

    for row in rows:

        src_id = row["src_node_id"]
        tgt_id = row["tgt_node_id"]

        if src_id:

            label = normalize_label(row["src_entity_type"])

            grouped.setdefault(label, {})

            grouped[label][src_id] = {
                "node_id": str(src_id),
                "name": row["src_name_label"],
                "path": None}

        if tgt_id:

            label = normalize_label(row["tgt_entity_type"])

            grouped.setdefault(label, {})

            grouped[label][tgt_id] = {
                "node_id": str(tgt_id),
                "name": row["tgt_name_label"],
                "path": row["tgt_path"]
            }

    total = sum(len(v) for v in grouped.values())

    print(f"Unique nodes: {total:,}")

    with neo4j_driver.session() as session:

        for label, node_map in grouped.items():

            print(f"\nImporting label: {label}")

            query = f"""
            UNWIND $rows AS row

            MERGE (n:{label} {{node_id: row.node_id}})

            SET n.name = row.name,
                n.path = row.path
            """

            node_rows = list(node_map.values())

            for idx, batch in enumerate(chunked(node_rows, BATCH_SIZE), start=1):

                session.run(query, rows=batch)

                print(f"{label} batch {idx}")

    print("Nodes imported")


# ============================================================
# CREATE RELATIONSHIPS
# ============================================================

def create_relationships(rows):

    print("\n=== Creating relationships ===")

    grouped = {}

    skipped = 0

    for row in rows:

        src_id = row["src_node_id"]
        tgt_id = row["tgt_node_id"]

        if not src_id or not tgt_id:
            skipped += 1
            continue

        rel_type = normalize_rel(row["link_type"])

        grouped.setdefault(rel_type, [])

        grouped[rel_type].append({
            "src": str(src_id),
            "tgt": str(tgt_id)
        })

    print(f"Skipped invalid rows: {skipped:,}")

    with neo4j_driver.session() as session:

        for rel_type, rel_rows in grouped.items():

            print(f"\nImporting relationship: {rel_type}")
            print(f"Count: {len(rel_rows):,}")

            query = f"""
            UNWIND $rows AS row

            MATCH (src {{node_id: row.src}})
            MATCH (tgt {{node_id: row.tgt}})

            MERGE (src)-[r:{rel_type}]->(tgt)
            """

            for idx, batch in enumerate(chunked(rel_rows, BATCH_SIZE), start=1):

                session.run(query, rows=batch)

                print(f"{rel_type} batch {idx}")


# ============================================================
# CREATE DPI -> DP RELATIONSHIPS
# ============================================================

def create_part_of_relationships():

    print("\n=== Linking DataProcessingItem -> DataProcessing ===")

    query = r"""
    MATCH (dpi:DataProcessingItem)

    WHERE dpi.path IS NOT NULL

    WITH dpi, split(dpi.path, '\\') AS parts

    WITH dpi,
         CASE
            WHEN size(parts) >= 2
            THEN parts[size(parts)-2]
            ELSE NULL
         END AS dp_name

    WHERE dp_name IS NOT NULL

    MATCH (dp:DataProcessing)
    WHERE trim(dp.name) = trim(dp_name)

    MERGE (dpi)-[:PART_OF]->(dp)
    """

    with neo4j_driver.session() as session:

        session.run(query)

    print("PART_OF relationships created")


# ============================================================
# VALIDATION
# ============================================================

def validate():

    print("\n=== VALIDATION ===")

    with neo4j_driver.session() as session:

        rel_count = session.run("""
            MATCH ()-[r]->()
            RETURN count(r) AS c
        """).single()["c"]

        node_count = session.run("""
            MATCH (n)
            RETURN count(n) AS c
        """).single()["c"]

        print(f"Neo4j nodes: {node_count:,}")
        print(f"Neo4j relationships: {rel_count:,}")

        result = session.run("""
            MATCH ()-[r]->()
            RETURN type(r) AS rel, count(*) AS c
            ORDER BY c DESC
        """)

        print("\nNeo4j relationship distribution:")

        for row in result:
            print(f"{row['rel']}: {row['c']:,}")


# ============================================================
# MAIN
# ============================================================

def main():

    print("====================================")
    print("LINK → NEO4J IMPORTER")
    print("====================================")

    rows = load_links()

    ensure_constraints()

    create_nodes(rows)

    create_relationships(rows)

    create_part_of_relationships()

    validate()

    neo4j_driver.close()

    print("\n=== IMPORT COMPLETE ===")


# ============================================================

if __name__ == "__main__":
    main()
