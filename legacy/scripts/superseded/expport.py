from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy import create_engine, text


# =============================================================================
# CONFIG
# =============================================================================

POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql+psycopg2://postgres:change_me@localhost/DataGalaxy_tables",
)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "change_me")

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1000"))


# =============================================================================
# CONNECTIONS
# =============================================================================

pg_engine = create_engine(
    POSTGRES_URL,
    pool_pre_ping=True,
)

neo4j = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    connection_timeout=60,
    max_connection_lifetime=3600,
)


# =============================================================================
# HELPERS
# =============================================================================

def clean_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def fetch_all(query: str) -> list[dict[str, Any]]:
    with pg_engine.connect() as conn:
        result = conn.execute(text(query))
        columns = list(result.keys())

        return [
            {
                col: clean_value(value)
                for col, value in zip(columns, row)
            }
            for row in result.fetchall()
        ]


def run_cypher(query: str, rows: list[dict[str, Any]] | None = None) -> None:
    with neo4j.session() as session:
        result = session.run(query, rows=rows or [])
        result.consume()


def run_cypher_batches(
    query: str,
    rows: list[dict[str, Any]],
    batch_size: int = BATCH_SIZE,
) -> int:

    total = len(rows)

    if total == 0:
        return 0

    done = 0

    for i in range(0, total, batch_size):

        batch = rows[i:i + batch_size]

        run_cypher(query, batch)

        done += len(batch)

        print(
            f"    batch {i // batch_size + 1}: "
            f"{done}/{total}"
        )

    return done


# =============================================================================
# CONSTRAINTS + INDEXES
# =============================================================================

def ensure_constraints() -> None:

    print("\nEnsuring Neo4j constraints...")

    queries = [

        # ============================================================
        # UNIQUE IDS
        # ============================================================

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
        CREATE CONSTRAINT bt_term_id IF NOT EXISTS
        FOR (n:BusinessTerm)
        REQUIRE n.term_id IS UNIQUE
        """,
    ]

    for query in queries:
        run_cypher(query)

    print("Constraints created.")


# =============================================================================
# LOAD NODES
# =============================================================================

def load_nodes() -> None:

    mappings = [

        ("source", "Source", "node_id"),
        ("container", "Container", "node_id"),
        ("structure", "Structure", "node_id"),
        ("field", "Field", "node_id"),

    ]

    for table_name, label, pk in mappings:

        print(f"\nLoading {label} nodes...")

        rows = fetch_all(f"""
            SELECT *
            FROM {table_name}
            WHERE {pk} IS NOT NULL
        """)

        print(f"  rows: {len(rows)}")

        query = f"""
        UNWIND $rows AS row

        MERGE (n:{label} {{
            node_id: row.{pk}
        }})

        SET n += row
        """

        run_cypher_batches(query, rows)


# =============================================================================
# LOAD USAGE NODES
# =============================================================================

def load_usage_nodes() -> None:

    print("\nLoading Usage nodes...")

    rows = fetch_all("""
        SELECT *
        FROM usage
        WHERE usage_uuid IS NOT NULL
    """)

    print(f"  rows: {len(rows)}")

    query = """
    UNWIND $rows AS row

    MERGE (u:Usage {
        usage_uuid: row.usage_uuid
    })

    SET u += row
    """

    run_cypher_batches(query, rows)


# =============================================================================
# SOURCE -> CONTAINER
# CONTAINER -> CONTAINER
# =============================================================================

def create_container_relationships() -> None:

    print("\nCreating Container hierarchy...")

    rows = fetch_all("""
        SELECT
            node_id,
            parent_node_id
        FROM container
        WHERE node_id IS NOT NULL
          AND parent_node_id IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row

    MATCH (child:Container {
        node_id: row.node_id
    })

    OPTIONAL MATCH (src:Source {
        node_id: row.parent_node_id
    })

    OPTIONAL MATCH (parent_container:Container {
        node_id: row.parent_node_id
    })

    FOREACH (_ IN CASE WHEN src IS NOT NULL THEN [1] ELSE [] END |

        MERGE (src)-[:HAS_CONTAINER]->(child)

    )

    FOREACH (_ IN CASE WHEN parent_container IS NOT NULL THEN [1] ELSE [] END |

        MERGE (parent_container)-[:HAS_CONTAINER]->(child)

    )
    """

    run_cypher_batches(query, rows)


# =============================================================================
# STRUCTURE RELATIONSHIPS
#
# IMPORTANT:
# Structures may have:
#
#   Source     parent
#   Container  parent
#   Structure  parent
#
# =============================================================================

def create_structure_relationships() -> None:

    print("\nCreating Structure hierarchy...")

    rows = fetch_all("""
        SELECT
            node_id,
            parent_node_id
        FROM structure
        WHERE node_id IS NOT NULL
          AND parent_node_id IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row

    MATCH (child:Structure {
        node_id: row.node_id
    })

    OPTIONAL MATCH (src:Source {
        node_id: row.parent_node_id
    })

    OPTIONAL MATCH (cnt:Container {
        node_id: row.parent_node_id
    })

    OPTIONAL MATCH (st:Structure {
        node_id: row.parent_node_id
    })

    FOREACH (_ IN CASE WHEN src IS NOT NULL THEN [1] ELSE [] END |

        MERGE (src)-[:HAS_STRUCTURE]->(child)

    )

    FOREACH (_ IN CASE WHEN cnt IS NOT NULL THEN [1] ELSE [] END |

        MERGE (cnt)-[:HAS_STRUCTURE]->(child)

    )

    FOREACH (_ IN CASE WHEN st IS NOT NULL THEN [1] ELSE [] END |

        MERGE (st)-[:HAS_STRUCTURE]->(child)

    )
    """

    run_cypher_batches(query, rows)


# =============================================================================
# FIELD RELATIONSHIPS
#
# IMPORTANT:
# Fields may have:
#
#   Structure parent
#   Container parent
#   Source parent
#
# =============================================================================

def create_field_relationships() -> None:

    print("\nCreating Field hierarchy...")

    rows = fetch_all("""
        SELECT
            node_id,
            parent_node_id
        FROM field
        WHERE node_id IS NOT NULL
          AND parent_node_id IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row

    MATCH (child:Field {
        node_id: row.node_id
    })

    OPTIONAL MATCH (st:Structure {
        node_id: row.parent_node_id
    })

    OPTIONAL MATCH (cnt:Container {
        node_id: row.parent_node_id
    })

    OPTIONAL MATCH (src:Source {
        node_id: row.parent_node_id
    })

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

    run_cypher_batches(query, rows)


# =============================================================================
# FIELD -> BUSINESS TERM
# =============================================================================

def create_business_term_relationships() -> None:

    print("\nCreating BusinessTerm relationships...")

    rows = fetch_all("""
        SELECT
            src_node_id,
            tgt_node_id,
            tgt_name_label,
            tgt_name_tech,
            tgt_entity_type,
            tgt_data_type,
            tgt_path,
            link_type
        FROM link
        WHERE src_node_id IS NOT NULL
          AND tgt_node_id IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row

    MATCH (f:Field {
        node_id: row.src_node_id
    })

    MERGE (bt:BusinessTerm {
        term_id: row.tgt_node_id
    })

    SET
        bt.name_label = row.tgt_name_label,
        bt.name_tech = row.tgt_name_tech,
        bt.entity_type = row.tgt_entity_type,
        bt.data_type = row.tgt_data_type,
        bt.path_full = row.tgt_path

    MERGE (f)-[r:IMPLEMENTS]->(bt)

    SET r.link_type = row.link_type
    """

    run_cypher_batches(query, rows)


# =============================================================================
# USAGE RELATIONSHIPS
# =============================================================================

def create_usage_relationships() -> None:

    print("\nCreating Usage -> Source relationships...")

    rows = fetch_all("""
        SELECT
            usage_uuid,
            app_code
        FROM usage
        WHERE usage_uuid IS NOT NULL
          AND app_code IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row

    MATCH (u:Usage {
        usage_uuid: row.usage_uuid
    })

    MATCH (s:Source {
        app_code: row.app_code
    })

    MERGE (u)-[r:USES]->(s)

    SET r.match_method = 'app_code'
    """

    run_cypher_batches(query, rows)

    print("\nCreating Usage -> Structure relationships...")

    rows = fetch_all("""
        SELECT
            usage_uuid,
            dataset_ref
        FROM usage
        WHERE usage_uuid IS NOT NULL
          AND dataset_ref IS NOT NULL
    """)

    query = """
    UNWIND $rows AS row

    MATCH (u:Usage {
        usage_uuid: row.usage_uuid
    })

    MATCH (st:Structure)

    WHERE
           toLower(coalesce(st.name_label, '')) = toLower(row.dataset_ref)
        OR toLower(coalesce(st.name_tech, '')) = toLower(row.dataset_ref)
        OR coalesce(st.path_full, '') CONTAINS row.dataset_ref

    MERGE (u)-[r:USES]->(st)

    SET r.match_method = 'dataset_ref'
    """

    run_cypher_batches(query, rows, batch_size=100)


# =============================================================================
# ORPHAN CHECK
# =============================================================================

def report_orphans() -> None:

    print("\nChecking unresolved parent relationships...")

    query = """
    MATCH (n)
    WHERE
        (n:Container OR n:Structure OR n:Field)
        AND n.parent_node_id IS NOT NULL
        AND NOT EXISTS {
            MATCH (p)
            WHERE p.node_id = n.parent_node_id
        }

    RETURN labels(n) AS labels,
           count(n) AS count

    ORDER BY count DESC
    """

    with neo4j.session() as session:

        records = list(session.run(query))

    if not records:
        print("  No unresolved parents.")
        return

    for record in records:
        print(f"  {record['labels']}: {record['count']}")


# =============================================================================
# STATS
# =============================================================================

def print_stats() -> None:

    print("\nNeo4j stats:\n")

    queries = [

        ("Sources",
         "MATCH (n:Source) RETURN count(n) AS c"),

        ("Containers",
         "MATCH (n:Container) RETURN count(n) AS c"),

        ("Structures",
         "MATCH (n:Structure) RETURN count(n) AS c"),

        ("Fields",
         "MATCH (n:Field) RETURN count(n) AS c"),

        ("BusinessTerms",
         "MATCH (n:BusinessTerm) RETURN count(n) AS c"),

        ("Usages",
         "MATCH (n:Usage) RETURN count(n) AS c"),

        ("Relationships",
         "MATCH ()-[r]->() RETURN count(r) AS c"),

        ("HAS_CONTAINER",
         "MATCH ()-[r:HAS_CONTAINER]->() RETURN count(r) AS c"),

        ("HAS_STRUCTURE",
         "MATCH ()-[r:HAS_STRUCTURE]->() RETURN count(r) AS c"),

        ("HAS_FIELD",
         "MATCH ()-[r:HAS_FIELD]->() RETURN count(r) AS c"),

        ("IMPLEMENTS",
         "MATCH ()-[r:IMPLEMENTS]->() RETURN count(r) AS c"),

        ("USES",
         "MATCH ()-[r:USES]->() RETURN count(r) AS c"),

    ]

    with neo4j.session() as session:

        for label, query in queries:

            count = session.run(query).single()["c"]

            print(f"{label:<20}: {count}")


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:

    print("\nPostgreSQL -> Neo4j lineage migration")
    print("=" * 70)

    ensure_constraints()

    # ---------------------------------------------------------
    # NODES
    # ---------------------------------------------------------

    load_nodes()
    load_usage_nodes()

    # ---------------------------------------------------------
    # HIERARCHY
    # ---------------------------------------------------------

    create_container_relationships()
    create_structure_relationships()
    create_field_relationships()

    # ---------------------------------------------------------
    # LINEAGE
    # ---------------------------------------------------------

    create_business_term_relationships()

    # ---------------------------------------------------------
    # USAGE
    # ---------------------------------------------------------

    create_usage_relationships()

    # ---------------------------------------------------------
    # VALIDATION
    # ---------------------------------------------------------

    report_orphans()

    print_stats()

    neo4j.close()

    print("\nMigration completed successfully.")


if __name__ == "__main__":
    main()