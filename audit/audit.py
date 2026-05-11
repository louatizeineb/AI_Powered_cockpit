import psycopg2
from neo4j import GraphDatabase
from collections import defaultdict

PG_CONN = {
    "host": "localhost",
    "port": 5432,
    "dbname": "DataGalaxy_tables",
    "user": "postgres",
    "password": "louatiza"
}

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "bpi_cockpit"

pg_conn = psycopg2.connect(**PG_CONN)
pg_cursor = pg_conn.cursor()

neo_driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD)
)

def load_postgres_links():
    print("\n Loading Postgres lineage")

    query = """
    SELECT 
        src_node_id,
        tgt_node_id,
        link_type
    FROM dg_link
    WHERE link_type IN ('IsInputOf', 'IsOutputOf')
    """

    pg_cursor.execute(query)

    links = []
    for row in pg_cursor.fetchall():
        links.append({
            "src": row[0],
            "tgt": row[1],
            "type": row[2]
        })

    print(f" Loaded {len(links)} links from Postgres")
    return links

def load_neo4j_relationships():

    print("\nLoading Neo4j lineage")

    with neo_driver.session() as session:

        query = """
        MATCH (a)-[r]->(b)
        WHERE type(r) IN ['IS_INPUT_OF', 'IS_OUTPUT_OF']
        RETURN 
            a.node_id AS src,
            b.node_id AS tgt,
            type(r) AS type
        """

        result = session.run(query)

        rels = []
        for r in result:
            rels.append({
                "src": r["src"],
                "tgt": r["tgt"],
                "type": r["type"]
            })

    print(f"Loaded {len(rels)} relationships from Neo4j")
    return rels


def normalize_type(t):
    return {
        "IsInputOf": "IS_INPUT_OF",
        "IsOutputOf": "IS_OUTPUT_OF"
    }.get(t, t)


def validate():

    pg_links = load_postgres_links()
    neo_links = load_neo4j_relationships()

    pg_set = set(
        (l["src"], l["tgt"], normalize_type(l["type"]))
        for l in pg_links
    )

    neo_set = set(
        (l["src"], l["tgt"], l["type"])
        for l in neo_links
    )

    missing_in_neo = pg_set - neo_set

    extra_in_neo = neo_set - pg_set


    print("\n VALIDATION REPORT")

    print(f"Postgres links       : {len(pg_set)}")
    print(f"Neo4j relationships  : {len(neo_set)}")

    print(f"\n Missing in Neo4j  : {len(missing_in_neo)}")
    print(f" Extra in Neo4j    : {len(extra_in_neo)}")

    print("\n Sample missing relationships:")
    for i, rel in enumerate(list(missing_in_neo)[:10]):
        print(rel)

    print("\n Sample extra relationships:")
    for i, rel in enumerate(list(extra_in_neo)[:10]):
        print(rel)



    print("\nChecking missing nodes...")

    with neo_driver.session() as session:

        missing_nodes = set()

        for src, tgt, _ in missing_in_neo:
            for node_id in [src, tgt]:

                result = session.run(
                    "MATCH (n {node_id: $id}) RETURN n LIMIT 1",
                    id=node_id
                )

                if result.single() is None:
                    missing_nodes.add(node_id)

        print(f"\n Missing nodes in Neo4j: {len(missing_nodes)}")

        for node in list(missing_nodes)[:10]:
            print(node)


if __name__ == "__main__":
    validate()