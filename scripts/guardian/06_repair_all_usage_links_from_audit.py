from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

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

POSTGRES_TABLE_PREFIX = os.getenv("POSTGRES_TABLE_PREFIX", "auto")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10000"))

OUTPUT_DIR = Path(
    os.getenv(
        "REPAIR_OUTPUT_DIR",
        "reports/migration_guardian/usage_links_repair",
    )
)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# RELATIONSHIP MAPPING
# =============================================================================

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

USAGE_RELEVANT_REL_TYPES = {
    "USES",
    "IS_USED_BY",
    "IS_USAGE_SOURCE_FOR",
    "IS_USAGE_DESTINATION_FOR",
    "HAS_FOR_SOURCE",
    "IS_SOURCE_OF",
    "IS_LINKED_TO",
    "CALLS",
    "IS_CALLED_BY",
    "IMPLEMENTS",
    "IS_IMPLEMENTED_BY",
    "GENERALIZES",
    "SPECIALIZES",
    "REGROUPS",
    "IS_PART_OF_DIMENSION",
    "HAS_FOR_UNIVERSE",
    "IS_UNIVERSE_OF",
    "IS_USED_FOR_COMPUTATION_OF",
    "IS_INPUT_OF",
    "IS_OUTPUT_OF",
}


# =============================================================================
# CONNECTIONS
# =============================================================================

pg = create_engine(POSTGRES_URL, pool_pre_ping=True)

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


def normalize_rel(value: Any) -> str:
    if value is None:
        return "RELATED_TO"

    text = str(value).strip()
    if not text:
        return "RELATED_TO"

    if text in REL_MAPPING:
        return REL_MAPPING[text]

    # Fallback for unknown camelCase values.
    rel = re.sub(r"[^A-Za-z0-9_]", "_", text)
    rel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", rel)
    rel = rel.upper()
    rel = re.sub(r"_+", "_", rel).strip("_")

    return rel or "RELATED_TO"


def endpoint_is_usage_like(row: dict[str, Any], side: str) -> bool:
    entity_type = str(row.get(f"{side}_entity_type") or "").lower()
    data_type = str(row.get(f"{side}_data_type") or "").lower()
    path_type = str(row.get(f"{side}_path_type") or "").lower()
    path = str(row.get(f"{side}_path") or "").lower()
    name_label = str(row.get(f"{side}_name_label") or "").lower()
    name_tech = str(row.get(f"{side}_name_tech") or "").lower()

    blob = " ".join(
        [
            entity_type,
            data_type,
            path_type,
            path,
            name_label,
            name_tech,
        ]
    )

    tokens = [
        "usage",
        "usag",
        "operationalusage",
        "operational usage",
        "utilisation",
        "dashboard",
        "report",
        "rapport",
    ]

    return any(token in blob for token in tokens)


def fetch_pg(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with pg.connect() as conn:
        result = conn.execute(text(query), params or {})
        return [
            {key: clean_value(value) for key, value in row.items()}
            for row in result.mappings().all()
        ]


def run_neo4j(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with neo4j.session() as session:
        result = session.run(query, **(params or {}))
        rows = [dict(record) for record in result]
        result.consume()
        return rows


def chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


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


def ensure_indexes() -> None:
    print("[NEO4J] Ensuring indexes...")

    queries = [
        """
        CREATE INDEX dg_object_node_id IF NOT EXISTS
        FOR (n:DataGalaxyObject)
        ON (n.node_id)
        """,
        """
        CREATE INDEX usage_node_id IF NOT EXISTS
        FOR (u:Usage)
        ON (u.node_id)
        """,
        """
        CREATE CONSTRAINT usage_uuid IF NOT EXISTS
        FOR (u:Usage)
        REQUIRE u.usage_uuid IS UNIQUE
        """,
    ]

    for query in queries:
        run_neo4j(query)


# =============================================================================
# LOAD USAGE-RELEVANT LINK ROWS
# =============================================================================

def load_usage_relevant_link_rows(link_table: str) -> list[dict[str, Any]]:
    print("[PG] Loading usage-relevant relationships from link table...")

    rows = fetch_pg(
        f"""
        SELECT
            export_date,
            workspace_id,

            src_node_id,
            src_name_label,
            src_name_tech,
            src_entity_type,
            src_data_type,

            link_type,

            tgt_node_id,
            tgt_name_label,
            tgt_name_tech,
            tgt_entity_type,
            tgt_data_type,
            tgt_path_type,
            tgt_path
        FROM {link_table}
        WHERE src_node_id IS NOT NULL
          AND tgt_node_id IS NOT NULL
        """
    )

    selected = []

    for row in rows:
        rel = normalize_rel(row.get("link_type"))
        row["normalized_relationship"] = rel

        src_is_usage = endpoint_is_usage_like(row, "src")
        tgt_is_usage = endpoint_is_usage_like(row, "tgt")

        row["src_is_usage_like"] = src_is_usage
        row["tgt_is_usage_like"] = tgt_is_usage

        if src_is_usage or tgt_is_usage or rel in USAGE_RELEVANT_REL_TYPES:
            selected.append(row)

    print(f"[PG] Total link rows: {len(rows):,}")
    print(f"[PG] Usage-relevant rows selected: {len(selected):,}")

    print("[PG] Selected relationship distribution:")
    for rel, count in Counter(row["normalized_relationship"] for row in selected).most_common():
        print(f"  {rel:<35} {count:,}")

    return selected


# =============================================================================
# ENSURE ENDPOINT NODES
# =============================================================================

def build_endpoint_nodes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}

    for row in rows:
        src_id = row.get("src_node_id")
        tgt_id = row.get("tgt_node_id")

        if src_id:
            nodes[str(src_id)] = {
                "node_id": str(src_id),
                "name_label": row.get("src_name_label"),
                "name_tech": row.get("src_name_tech"),
                "entity_type": row.get("src_entity_type"),
                "data_type": row.get("src_data_type"),
                "path_type": None,
                "path_full": None,
                "is_usage_like": bool(row.get("src_is_usage_like")),
            }

        if tgt_id:
            nodes[str(tgt_id)] = {
                "node_id": str(tgt_id),
                "name_label": row.get("tgt_name_label"),
                "name_tech": row.get("tgt_name_tech"),
                "entity_type": row.get("tgt_entity_type"),
                "data_type": row.get("tgt_data_type"),
                "path_type": row.get("tgt_path_type"),
                "path_full": row.get("tgt_path"),
                "is_usage_like": bool(row.get("tgt_is_usage_like")),
            }

    return list(nodes.values())


def merge_endpoint_nodes(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    print(f"[REPAIR] Ensuring endpoint nodes exist: {len(nodes):,}")

    query = """
    UNWIND $rows AS row

    MERGE (n:DataGalaxyObject {node_id: row.node_id})

    SET n:LineageNode,
        n.name_label = coalesce(n.name_label, row.name_label),
        n.name_tech = coalesce(n.name_tech, row.name_tech),
        n.entity_type = coalesce(n.entity_type, row.entity_type),
        n.data_type = coalesce(n.data_type, row.data_type),
        n.path_type = coalesce(n.path_type, row.path_type),
        n.path_full = coalesce(n.path_full, row.path_full),
        n.imported_from = coalesce(n.imported_from, 'link'),
        n.repaired_by = '06_repair_all_usage_links_from_audit.py'

    FOREACH (_ IN CASE WHEN row.is_usage_like THEN [1] ELSE [] END |
        SET n:Usage,
            n.usage_uuid = coalesce(n.usage_uuid, row.node_id),
            n.node_id = coalesce(n.node_id, row.node_id),
            n.usage_name = coalesce(n.usage_name, row.name_label),
            n.usage_tech_name = coalesce(n.usage_tech_name, row.name_tech)
    )
    """

    done = 0

    for batch_index, batch in enumerate(chunks(nodes, BATCH_SIZE), start=1):
        run_neo4j(query, {"rows": batch})
        done += len(batch)
        print(f"  endpoint batch {batch_index}: {done:,}/{len(nodes):,}", flush=True)

    return {
        "endpoint_nodes_seen": len(nodes),
        "endpoint_nodes_merged": done,
    }


# =============================================================================
# MERGE RELATIONSHIPS
# =============================================================================

def merge_relationships(rows: list[dict[str, Any]]) -> dict[str, Any]:
    print("[REPAIR] Merging usage-relevant relationships...")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        grouped[row["normalized_relationship"]].append(row)

    summary = {}

    for rel_type, rel_rows in sorted(grouped.items()):
        print(f"  relationship {rel_type}: {len(rel_rows):,}")

        # Relationship type cannot be parameterized in Cypher, so it is injected
        # after normalization and strict sanitization.
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", rel_type):
            raise ValueError(f"Unsafe relationship type: {rel_type}")

        query = f"""
        UNWIND $rows AS row

        MATCH (src:DataGalaxyObject {{node_id: row.src_node_id}})
        MATCH (tgt:DataGalaxyObject {{node_id: row.tgt_node_id}})

        MERGE (src)-[r:{rel_type}]->(tgt)

        SET r.link_type = row.link_type,
            r.workspace_id = row.workspace_id,
            r.export_date = row.export_date,
            r.imported_from = 'link',
            r.repaired_by = '06_repair_all_usage_links_from_audit.py'
        """

        done = 0

        for batch_index, batch in enumerate(chunks(rel_rows, BATCH_SIZE), start=1):
            run_neo4j(query, {"rows": batch})
            done += len(batch)
            print(f"    batch {batch_index}: {done:,}/{len(rel_rows):,}", flush=True)

        summary[rel_type] = len(rel_rows)

    return summary


# =============================================================================
# CLEAN WRONG RELATIONSHIP TYPES OPTIONAL
# =============================================================================

def report_wrong_relationship_types() -> list[dict[str, Any]]:
    wrong_types = [
        "ISLINKEDTO",
        "HASFORUNIVERSE",
        "ISUNIVERSEOF",
        "ISCALLEDBY",
        "ISIMPLEMENTEDBY",
        "ISUSEDFORCOMPUTATIONOF",
        "ISPARTOFDIMENSION",
    ]

    rows = []

    for rel_type in wrong_types:
        query = f"""
        MATCH ()-[r:{rel_type}]->()
        RETURN '{rel_type}' AS relationship_type, count(r) AS count
        """
        result = run_neo4j(query)
        if result:
            rows.extend(result)

    output_file = OUTPUT_DIR / "wrong_relationship_type_counts.csv"

    with output_file.open("w", encoding="utf-8", newline="") as f:
        fieldnames = ["relationship_type", "count"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return rows


# =============================================================================
# POST REPAIR STATS
# =============================================================================

def post_stats() -> dict[str, Any]:
    rel_rows = run_neo4j(
        """
        MATCH ()-[r]->()
        WHERE type(r) IN [
            'USES',
            'IS_USED_BY',
            'IS_USAGE_SOURCE_FOR',
            'IS_USAGE_DESTINATION_FOR',
            'HAS_FOR_SOURCE',
            'IS_SOURCE_OF',
            'IS_LINKED_TO',
            'CALLS',
            'IS_CALLED_BY',
            'IMPLEMENTS',
            'IS_IMPLEMENTED_BY',
            'GENERALIZES',
            'SPECIALIZES',
            'REGROUPS',
            'IS_PART_OF_DIMENSION',
            'HAS_FOR_UNIVERSE',
            'IS_UNIVERSE_OF',
            'IS_USED_FOR_COMPUTATION_OF',
            'IS_INPUT_OF',
            'IS_OUTPUT_OF'
        ]
        RETURN type(r) AS relationship_type, count(r) AS count
        ORDER BY count DESC
        """
    )

    usage_count = run_neo4j(
        """
        MATCH (u:Usage)
        RETURN count(u) AS count
        """
    )[0]["count"]

    data_galaxy_count = run_neo4j(
        """
        MATCH (n:DataGalaxyObject)
        RETURN count(n) AS count
        """
    )[0]["count"]

    return {
        "usage_nodes": int(usage_count or 0),
        "data_galaxy_objects": int(data_galaxy_count or 0),
        "relationship_distribution": rel_rows,
    }


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("=" * 80)
    print("REPAIR ALL USAGE-RELEVANT LINKS FROM POSTGRES LINK TABLE")
    print("=" * 80)

    ensure_indexes()

    available_tables = existing_pg_tables()
    link_table = resolve_table("link", available_tables)

    if link_table is None:
        raise RuntimeError("Could not find link or dg_link table.")

    print(f"[INFO] Link table: {link_table}")

    link_rows = load_usage_relevant_link_rows(link_table)
    endpoint_nodes = build_endpoint_nodes(link_rows)

    endpoint_summary = merge_endpoint_nodes(endpoint_nodes)
    relationship_summary = merge_relationships(link_rows)
    wrong_rel_report = report_wrong_relationship_types()
    stats = post_stats()

    final_summary = {
        "link_table": link_table,
        "usage_relevant_link_rows": len(link_rows),
        "endpoint_summary": endpoint_summary,
        "relationship_summary": relationship_summary,
        "wrong_relationship_type_counts": wrong_rel_report,
        "post_stats": stats,
        "output_dir": str(OUTPUT_DIR),
    }

    summary_file = OUTPUT_DIR / "usage_links_repair_summary.json"
    summary_file.write_text(
        json.dumps(final_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 80)
    print("USAGE LINK REPAIR COMPLETE")
    print("=" * 80)
    print(json.dumps(final_summary, indent=2, ensure_ascii=False))

    neo4j.close()


if __name__ == "__main__":
    main()