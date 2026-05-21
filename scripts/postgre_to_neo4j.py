from sqlalchemy import create_engine, text
from neo4j import GraphDatabase
from datetime import date, datetime
import os

POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql+psycopg2://postgres:louatiza@localhost/DataGalaxy_tables"
)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "bpi_cockpit")

BATCH_SIZE = 2000


pg_engine = create_engine(POSTGRES_URL)
neo4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def clean_value(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def clean_record(row, columns):
    return {col: clean_value(value) for col, value in zip(columns, row)}


def fetch_all(query):
    with pg_engine.connect() as conn:
        result = conn.execute(text(query))
        columns = result.keys()
        return [clean_record(row, columns) for row in result.fetchall()]


def run_cypher(query, rows=None):
    with neo4j.session() as session:
        result = session.run(query, rows=rows or [])
        result.consume()

def run_cypher_batches(query, rows, batch_size=200):
    total = len(rows)
    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        run_cypher(query, batch)
        print(f"    batch {i // batch_size + 1}: {len(batch)} rows")


def create_constraints():
    queries = [
        "CREATE CONSTRAINT source_id IF NOT EXISTS FOR (n:Source) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT container_id IF NOT EXISTS FOR (n:Container) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT structure_id IF NOT EXISTS FOR (n:Structure) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT field_id IF NOT EXISTS FOR (n:Field) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT usage_id IF NOT EXISTS FOR (n:Usage) REQUIRE n.usage_uuid IS UNIQUE",
        "CREATE CONSTRAINT term_id IF NOT EXISTS FOR (n:BusinessTerm) REQUIRE n.term_id IS UNIQUE",
    ]
    for q in queries:
        run_cypher(q)


def load_nodes():
    mappings = [
        ("dg_source", "Source", "node_id"),
        ("dg_container", "Container", "node_id"),
        ("dg_structure", "Structure", "node_id"),
        ("dg_field", "Field", "node_id"),
    ]

    for table, label, pk in mappings:
        rows = fetch_all(f"SELECT * FROM {table}")
        rows = [r for r in rows if r.get(pk)]

        print(f"Loading {len(rows)} {label} nodes")

        query = f"""
        UNWIND $rows AS row
        MERGE (n:{label} {{node_id: row.{pk}}})
        SET n += row
        """

        run_cypher_batches(query, rows)

    usage_rows = fetch_all("SELECT * FROM dg_usage")
    usage_rows = [r for r in usage_rows if r.get("usage_uuid")]

    print(f"Loading {len(usage_rows)} Usage nodes")

    query = """
    UNWIND $rows AS row
    MERGE (u:Usage {usage_uuid: row.usage_uuid})
    SET u += row
    """

    run_cypher_batches(query, usage_rows)


def load_hierarchy_relationships():
    print("Creating Source → Container relationships")

    rows = fetch_all("""
        SELECT node_id, parent_node_id
        FROM dg_container
        WHERE parent_node_id IS NOT NULL
    """)

    run_cypher("""
    UNWIND $rows AS row
    MATCH (parent:Source {node_id: row.parent_node_id})
    MATCH (child:Container {node_id: row.node_id})
    MERGE (parent)-[:CONTAINS]->(child)
    """, rows)

    print("Creating Container/Structure → Structure relationships")

    rows = fetch_all("""
        SELECT node_id, parent_node_id
        FROM dg_structure
        WHERE parent_node_id IS NOT NULL
    """)

    run_cypher("""
    UNWIND $rows AS row
    MATCH (child:Structure {node_id: row.node_id})
    OPTIONAL MATCH (container:Container {node_id: row.parent_node_id})
    OPTIONAL MATCH (structure:Structure {node_id: row.parent_node_id})
    FOREACH (_ IN CASE WHEN container IS NOT NULL THEN [1] ELSE [] END |
        MERGE (container)-[:CONTAINS]->(child)
    )
    FOREACH (_ IN CASE WHEN structure IS NOT NULL THEN [1] ELSE [] END |
        MERGE (structure)-[:CONTAINS]->(child)
    )
    """, rows)

    print("Creating Structure → Field relationships")

    rows = fetch_all("""
        SELECT node_id, parent_node_id
        FROM dg_field
        WHERE parent_node_id IS NOT NULL
    """)

    run_cypher("""
    UNWIND $rows AS row
    MATCH (parent:Structure {node_id: row.parent_node_id})
    MATCH (child:Field {node_id: row.node_id})
    MERGE (parent)-[:HAS_FIELD]->(child)
    """, rows)




def load_structure_field_relationships():
    print("Creating Structure → Field relationships")

    rows = fetch_all("""
        SELECT node_id, parent_node_id
        FROM dg_field
        WHERE parent_node_id IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row
    MATCH (parent:Structure {node_id: row.parent_node_id})
    MATCH (child:Field {node_id: row.node_id})
    MERGE (parent)-[:HAS_FIELD]->(child)
    """

    run_cypher_batches(query, rows, batch_size=200)





def load_business_terms():
    print("Creating Field → BusinessTerm relationships")

    rows = fetch_all("""
        SELECT src_node_id, tgt_node_id, tgt_name_label, tgt_name_tech,
               tgt_entity_type, tgt_data_type, tgt_path, link_type
        FROM dg_link
        WHERE src_node_id IS NOT NULL
          AND tgt_node_id IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row
    MATCH (f:Field {node_id: row.src_node_id})
    MERGE (bt:BusinessTerm {term_id: row.tgt_node_id})
    SET bt.name_label = row.tgt_name_label,
        bt.name_tech = row.tgt_name_tech,
        bt.entity_type = row.tgt_entity_type,
        bt.data_type = row.tgt_data_type,
        bt.path_full = row.tgt_path
    MERGE (f)-[r:IMPLEMENTS]->(bt)
    SET r.link_type = row.link_type
    """

    run_cypher_batches(query, rows, batch_size=100)


def load_usage_relationships():
    print("Creating Usage → Source relationships by app_code")

    rows = fetch_all("""
        SELECT usage_uuid, app_code
        FROM dg_usage
        WHERE usage_uuid IS NOT NULL
          AND app_code IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row
    MATCH (u:Usage {usage_uuid: row.usage_uuid})
    MATCH (s:Source {app_code: row.app_code})
    MERGE (u)-[r:USES]->(s)
    SET r.match_method = 'app_code'
    """

    run_cypher_batches(query, rows, batch_size=200)

    print("Creating Usage → Structure relationships by dataset_ref/name")

    rows = fetch_all("""
        SELECT usage_uuid, dataset_ref
        FROM dg_usage
        WHERE usage_uuid IS NOT NULL
          AND dataset_ref IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row
    MATCH (u:Usage {usage_uuid: row.usage_uuid})
    MATCH (st:Structure)
    WHERE toLower(st.name_label) = toLower(row.dataset_ref)
       OR toLower(st.name_tech) = toLower(row.dataset_ref)
       OR st.path_full CONTAINS row.dataset_ref
    MERGE (u)-[r:USES]->(st)
    SET r.match_method = 'dataset_ref'
    """

    run_cypher_batches(query, rows, batch_size=50)


def print_stats():
    queries = [
        ("Sources", "MATCH (n:Source) RETURN count(n) AS c"),
        ("Containers", "MATCH (n:Container) RETURN count(n) AS c"),
        ("Structures", "MATCH (n:Structure) RETURN count(n) AS c"),
        ("Fields", "MATCH (n:Field) RETURN count(n) AS c"),
        ("Usages", "MATCH (n:Usage) RETURN count(n) AS c"),
        ("BusinessTerms", "MATCH (n:BusinessTerm) RETURN count(n) AS c"),
        ("Relationships", "MATCH ()-[r]->() RETURN count(r) AS c"),
    ]

    with neo4j.session() as session:
        print("\nNeo4j stats:")
        for label, query in queries:
            count = session.run(query).single()["c"]
            print(f"  {label}: {count}")


"""if __name__ == "__main__":
    print("Starting PostgreSQL → Neo4j migration")

    #create_constraints()
    #load_nodes()
    #load_hierarchy_relationships()
    load_business_terms()
    load_usage_relationships()
    print_stats()

    neo4j.close()
    print("\nMigration completed.")"""

if __name__ == "__main__":
    print("Continuing BusinessTerm relationships")

    load_usage_relationships()
    print_stats()

    neo4j.close()