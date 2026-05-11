from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy import create_engine, text


POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql+psycopg2://postgres:louatiza@localhost/DataGalaxy_tables",
)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "bpi_cockpit")

BATCH_SIZE_CONTAINS = int(os.getenv("BATCH_SIZE_CONTAINS", "500"))
BATCH_SIZE_FIELDS = int(os.getenv("BATCH_SIZE_FIELDS", "200"))

pg_engine = create_engine(POSTGRES_URL)
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


def fetch_all(query: str) -> list[dict[str, Any]]:
    with pg_engine.connect() as conn:
        result = conn.execute(text(query))
        columns = list(result.keys())
        return [
            {col: clean_value(value) for col, value in zip(columns, row)}
            for row in result.fetchall()
        ]


def run_cypher(query: str, rows: list[dict[str, Any]] | None = None) -> None:
    with neo4j.session() as session:
        result = session.run(query, rows=rows or [])
        result.consume()


def run_cypher_batches(query: str, rows: list[dict[str, Any]], batch_size: int) -> int:
    total = len(rows)
    done = 0

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        run_cypher(query, batch)
        done += len(batch)
        print(f"    batch {i // batch_size + 1}: {done}/{total}")

    return done


def ensure_constraints() -> None:
    print("Ensuring constraints...")

    constraints = [
        "CREATE CONSTRAINT source_id IF NOT EXISTS FOR (n:Source) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT container_id IF NOT EXISTS FOR (n:Container) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT structure_id IF NOT EXISTS FOR (n:Structure) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT field_id IF NOT EXISTS FOR (n:Field) REQUIRE n.node_id IS UNIQUE",
    ]

    for query in constraints:
        run_cypher(query)


def repair_container_parent_relationships() -> None:
    print("\nCreating actual parent relationships for Container nodes...")

    rows = fetch_all("""
        SELECT node_id, parent_node_id
        FROM container
        WHERE node_id IS NOT NULL
          AND parent_node_id IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row
    MATCH (child:Container {node_id: row.node_id})
    OPTIONAL MATCH (src:Source {node_id: row.parent_node_id})
    OPTIONAL MATCH (cnt:Container {node_id: row.parent_node_id})

    FOREACH (_ IN CASE WHEN src IS NOT NULL THEN [1] ELSE [] END |
        MERGE (src)-[:CONTAINS]->(child)
    )

    FOREACH (_ IN CASE WHEN cnt IS NOT NULL THEN [1] ELSE [] END |
        MERGE (cnt)-[:CONTAINS]->(child)
    )
    """

    run_cypher_batches(query, rows, BATCH_SIZE_CONTAINS)


def repair_structure_parent_relationships() -> None:
    print("\nCreating actual parent relationships for Structure nodes...")

    rows = fetch_all("""
        SELECT node_id, parent_node_id
        FROM structure
        WHERE node_id IS NOT NULL
          AND parent_node_id IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row
    MATCH (child:Structure {node_id: row.node_id})
    OPTIONAL MATCH (src:Source {node_id: row.parent_node_id})
    OPTIONAL MATCH (cnt:Container {node_id: row.parent_node_id})
    OPTIONAL MATCH (st:Structure {node_id: row.parent_node_id})

    FOREACH (_ IN CASE WHEN src IS NOT NULL THEN [1] ELSE [] END |
        MERGE (src)-[:CONTAINS]->(child)
    )

    FOREACH (_ IN CASE WHEN cnt IS NOT NULL THEN [1] ELSE [] END |
        MERGE (cnt)-[:CONTAINS]->(child)
    )

    FOREACH (_ IN CASE WHEN st IS NOT NULL THEN [1] ELSE [] END |
        MERGE (st)-[:CONTAINS]->(child)
    )
    """

    run_cypher_batches(query, rows, BATCH_SIZE_CONTAINS)


def repair_field_parent_relationships() -> None:
    print("\nCreating actual parent relationships for Field nodes...")

    rows = fetch_all("""
        SELECT node_id, parent_node_id
        FROM field
        WHERE node_id IS NOT NULL
          AND parent_node_id IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row
    MATCH (child:Field {node_id: row.node_id})
    OPTIONAL MATCH (st:Structure {node_id: row.parent_node_id})
    OPTIONAL MATCH (cnt:Container {node_id: row.parent_node_id})
    OPTIONAL MATCH (src:Source {node_id: row.parent_node_id})

    FOREACH (_ IN CASE WHEN st IS NOT NULL THEN [1] ELSE [] END |
        MERGE (st)-[:HAS_FIELD]->(child)
    )

    FOREACH (_ IN CASE WHEN cnt IS NOT NULL THEN [1] ELSE [] END |
        MERGE (cnt)-[:HAS_FIELD]->(child)
    )

    FOREACH (_ IN CASE WHEN src IS NOT NULL THEN [1] ELSE [] END |
        MERGE (src)-[:HAS_FIELD]->(child)
    )
    """

    run_cypher_batches(query, rows, BATCH_SIZE_FIELDS)


def report_orphans() -> None:
    print("\nChecking unresolved parent_node_id values...")

    query = """
    MATCH (n)
    WHERE (n:Container OR n:Structure OR n:Field)
      AND n.parent_node_id IS NOT NULL
      AND NOT EXISTS {
          MATCH (p)
          WHERE p.node_id = n.parent_node_id
      }
    RETURN labels(n) AS labels, count(n) AS count
    ORDER BY count DESC
    """

    with neo4j.session() as session:
        records = list(session.run(query))

    if not records:
        print("  No unresolved parents found.")
        return

    for record in records:
        print(f"  {record['labels']}: {record['count']}")


def print_stats() -> None:
    print("\nNeo4j relationship stats:")

    queries = [
        ("CONTAINS", "MATCH ()-[r:CONTAINS]->() RETURN count(r) AS c"),
        ("HAS_FIELD", "MATCH ()-[r:HAS_FIELD]->() RETURN count(r) AS c"),
        ("Source direct children", "MATCH (:Source)-[r:CONTAINS|HAS_FIELD]->() RETURN count(r) AS c"),
        ("Container direct children", "MATCH (:Container)-[r:CONTAINS|HAS_FIELD]->() RETURN count(r) AS c"),
        ("Structure direct children", "MATCH (:Structure)-[r:CONTAINS|HAS_FIELD]->() RETURN count(r) AS c"),
    ]

    with neo4j.session() as session:
        for label, query in queries:
            count = session.run(query).single()["c"]
            print(f"  {label}: {count}")


def main() -> None:
    print("Repairing Neo4j hierarchy relationships from PostgreSQL parent_node_id")
    print("This script only creates relationships. It does not reload nodes.")

    ensure_constraints()
    repair_container_parent_relationships()
    repair_structure_parent_relationships()
    repair_field_parent_relationships()
    report_orphans()
    print_stats()

    neo4j.close()
    print("\nRelationship repair completed.")


if __name__ == "__main__":
    main()
