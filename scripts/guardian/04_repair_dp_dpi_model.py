from __future__ import annotations

import csv
import json
import os
import re
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
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10000"))

OUTPUT_DIR = Path(
    os.getenv(
        "REPAIR_OUTPUT_DIR",
        "reports/migration_guardian/dp_dpi_repair",
    )
)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


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


def normalize_path(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    parts = [part.strip() for part in re.split(r"\\+", text) if part.strip()]
    if not parts:
        return None

    return "\\".join(parts).lower()


def parent_path(value: Any) -> str | None:
    path = normalize_path(value)
    if not path:
        return None

    parts = path.split("\\")
    if len(parts) <= 1:
        return None

    return "\\".join(parts[:-1])


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
    print("[NEO4J] Ensuring DP/DPI indexes...")

    queries = [
        """
        CREATE CONSTRAINT dp_node_id IF NOT EXISTS
        FOR (n:DataProcessing)
        REQUIRE n.node_id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT dpi_node_id IF NOT EXISTS
        FOR (n:DataProcessingItem)
        REQUIRE n.node_id IS UNIQUE
        """,
        """
        CREATE INDEX dp_normalized_path IF NOT EXISTS
        FOR (n:DataProcessing)
        ON (n.normalized_path)
        """,
        """
        CREATE INDEX dpi_normalized_path IF NOT EXISTS
        FOR (n:DataProcessingItem)
        ON (n.normalized_path)
        """,
    ]

    for query in queries:
        run_neo4j(query)


def load_processing_nodes_from_postgres(link_table: str) -> list[dict[str, Any]]:
    print("[PG] Loading DP/DPI nodes from link table...")

    rows = fetch_pg(
        f"""
        SELECT DISTINCT
            tgt_node_id AS node_id,
            tgt_name_label AS name_label,
            tgt_name_tech AS name_tech,
            tgt_entity_type AS entity_type,
            tgt_data_type AS data_type,
            tgt_path_type AS path_type,
            tgt_path AS path_full
        FROM {link_table}
        WHERE tgt_node_id IS NOT NULL
          AND (
                tgt_entity_type IN ('DataProcessing', 'DataProcessingItem')
             OR tgt_data_type IN ('DataProcessing', 'DataProcessingItem')
          )
        """
    )

    for row in rows:
        entity_type = row.get("entity_type")
        data_type = row.get("data_type")

        row["label"] = (
            "DataProcessingItem"
            if entity_type == "DataProcessingItem" or data_type == "DataProcessingItem"
            else "DataProcessing"
        )
        row["normalized_path"] = normalize_path(row.get("path_full"))
        row["parent_dp_normalized_path"] = (
            parent_path(row.get("path_full"))
            if row["label"] == "DataProcessingItem"
            else None
        )

    print(f"[PG] DP/DPI nodes found: {len(rows):,}")
    return rows


def merge_processing_nodes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    print("[REPAIR] Merging missing DP/DPI nodes and normalized paths...")

    dp_rows = [row for row in rows if row["label"] == "DataProcessing"]
    dpi_rows = [row for row in rows if row["label"] == "DataProcessingItem"]

    # IMPORTANT:
    # Merge by the shared canonical identity first:
    # (:DataGalaxyObject {node_id})
    #
    # Do NOT MERGE directly on :DataProcessing or :DataProcessingItem,
    # because the same node may already exist as :DataGalaxyObject or :LineageNode.
    # Direct label-specific MERGE can create a duplicate and violate the
    # DataGalaxyObject(node_id) uniqueness constraint.

    dp_query = """
    UNWIND $rows AS row

    MERGE (n:DataGalaxyObject {node_id: row.node_id})

    SET n:DataProcessing,
        n:LineageNode,
        n.name_label = coalesce(row.name_label, n.name_label),
        n.name_tech = coalesce(row.name_tech, n.name_tech),
        n.entity_type = coalesce(row.entity_type, n.entity_type),
        n.data_type = coalesce(row.data_type, n.data_type),
        n.path_type = coalesce(row.path_type, n.path_type),
        n.path_full = coalesce(row.path_full, n.path_full),
        n.normalized_path = coalesce(row.normalized_path, n.normalized_path),
        n.imported_from = coalesce(n.imported_from, 'link'),
        n.repaired_by = '04_repair_dp_dpi_model.py'
    """

    dpi_query = """
    UNWIND $rows AS row

    MERGE (n:DataGalaxyObject {node_id: row.node_id})

    SET n:DataProcessingItem,
        n:LineageNode,
        n.name_label = coalesce(row.name_label, n.name_label),
        n.name_tech = coalesce(row.name_tech, n.name_tech),
        n.entity_type = coalesce(row.entity_type, n.entity_type),
        n.data_type = coalesce(row.data_type, n.data_type),
        n.path_type = coalesce(row.path_type, n.path_type),
        n.path_full = coalesce(row.path_full, n.path_full),
        n.normalized_path = coalesce(row.normalized_path, n.normalized_path),
        n.parent_dp_normalized_path = coalesce(row.parent_dp_normalized_path, n.parent_dp_normalized_path),
        n.imported_from = coalesce(n.imported_from, 'link'),
        n.repaired_by = '04_repair_dp_dpi_model.py'
    """

    done_dp = 0
    done_dpi = 0

    for batch_index, batch in enumerate(chunks(dp_rows, BATCH_SIZE), start=1):
        run_neo4j(dp_query, {"rows": batch})
        done_dp += len(batch)
        print(f"  DP batch {batch_index}: {done_dp:,}/{len(dp_rows):,}", flush=True)

    for batch_index, batch in enumerate(chunks(dpi_rows, BATCH_SIZE), start=1):
        run_neo4j(dpi_query, {"rows": batch})
        done_dpi += len(batch)
        print(f"  DPI batch {batch_index}: {done_dpi:,}/{len(dpi_rows):,}", flush=True)

    return {
        "data_processing_rows": len(dp_rows),
        "data_processing_item_rows": len(dpi_rows),
        "merge_strategy": "MERGE on :DataGalaxyObject(node_id), then add :DataProcessing or :DataProcessingItem label",
    }


def create_part_of_relationships() -> dict[str, Any]:
    print("[REPAIR] Creating DataProcessingItem -> DataProcessing PART_OF relationships...")

    query = """
    MATCH (dpi:DataProcessingItem)
    WHERE dpi.parent_dp_normalized_path IS NOT NULL

    OPTIONAL MATCH (dp:DataProcessing {normalized_path: dpi.parent_dp_normalized_path})

    WITH dpi, dp
    WHERE dp IS NOT NULL

    MERGE (dpi)-[r:PART_OF]->(dp)
    SET r.derivation = 'DPI_PARENT_PATH',
        r.repaired_by = '04_repair_dp_dpi_model.py'

    RETURN count(r) AS relationships_created_or_matched
    """

    result = run_neo4j(query)[0]

    unresolved_query = """
    MATCH (dpi:DataProcessingItem)
    WHERE dpi.parent_dp_normalized_path IS NOT NULL
      AND NOT EXISTS {
        MATCH (dpi)-[:PART_OF]->(:DataProcessing)
      }
    RETURN
        dpi.node_id AS dpi_node_id,
        dpi.name_label AS dpi_name_label,
        dpi.name_tech AS dpi_name_tech,
        dpi.path_full AS dpi_path_full,
        dpi.normalized_path AS dpi_normalized_path,
        dpi.parent_dp_normalized_path AS expected_parent_dp_path
    LIMIT 5000
    """

    unresolved = run_neo4j(unresolved_query)

    unresolved_file = OUTPUT_DIR / "unresolved_dpi_parent_after_repair.csv"

    with unresolved_file.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "dpi_node_id",
            "dpi_name_label",
            "dpi_name_tech",
            "dpi_path_full",
            "dpi_normalized_path",
            "expected_parent_dp_path",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(unresolved)

    return {
        "part_of_relationships_created_or_matched": int(result["relationships_created_or_matched"] or 0),
        "unresolved_dpi_parent_sample_count": len(unresolved),
        "unresolved_file": str(unresolved_file),
    }


def create_flows_to_relationships() -> dict[str, Any]:
    print("[REPAIR] Creating derived Field -> Field FLOWS_TO relationships via DPI...")

    query = """
    MATCH (input:Field)-[:IS_INPUT_OF]->(dpi:DataProcessingItem)<-[:IS_OUTPUT_OF]-(output:Field)
    OPTIONAL MATCH (dpi)-[:PART_OF]->(dp:DataProcessing)

    WITH input, output, dpi, dp
    WHERE input.node_id IS NOT NULL
      AND output.node_id IS NOT NULL
      AND dpi.node_id IS NOT NULL

    MERGE (input)-[r:FLOWS_TO {
        via_dpi_node_id: dpi.node_id
    }]->(output)

    SET r.via_dpi_name = coalesce(dpi.name_label, dpi.name_tech),
        r.via_dp_node_id = dp.node_id,
        r.via_dp_name = coalesce(dp.name_label, dp.name_tech),
        r.derivation = 'FIELD_INPUT_AND_OUTPUT_OF_SAME_DPI',
        r.confidence = 1.0,
        r.repaired_by = '04_repair_dp_dpi_model.py'

    RETURN count(r) AS flows_to_created_or_matched
    """

    result = run_neo4j(query)[0]

    sample_query = """
    MATCH (input:Field)-[r:FLOWS_TO]->(output:Field)
    RETURN
        input.node_id AS input_field_id,
        input.name_tech AS input_field_name,
        r.via_dpi_node_id AS via_dpi_node_id,
        r.via_dpi_name AS via_dpi_name,
        r.via_dp_node_id AS via_dp_node_id,
        r.via_dp_name AS via_dp_name,
        output.node_id AS output_field_id,
        output.name_tech AS output_field_name
    LIMIT 100
    """

    samples = run_neo4j(sample_query)

    sample_file = OUTPUT_DIR / "flows_to_samples_after_repair.csv"

    with sample_file.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "input_field_id",
            "input_field_name",
            "via_dpi_node_id",
            "via_dpi_name",
            "via_dp_node_id",
            "via_dp_name",
            "output_field_id",
            "output_field_name",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(samples)

    return {
        "flows_to_created_or_matched": int(result["flows_to_created_or_matched"] or 0),
        "sample_file": str(sample_file),
    }


def print_post_repair_stats() -> dict[str, Any]:
    stats_query = """
    RETURN
        size([(n:DataProcessing) | n]) AS data_processing_count,
        size([(n:DataProcessingItem) | n]) AS data_processing_item_count
    """

    # Some Neo4j versions do not support pattern comprehension in RETURN well at scale.
    dp = run_neo4j("MATCH (n:DataProcessing) RETURN count(n) AS count")[0]["count"]
    dpi = run_neo4j("MATCH (n:DataProcessingItem) RETURN count(n) AS count")[0]["count"]
    part_of = run_neo4j("MATCH (:DataProcessingItem)-[r:PART_OF]->(:DataProcessing) RETURN count(r) AS count")[0]["count"]
    flows_to = run_neo4j("MATCH (:Field)-[r:FLOWS_TO]->(:Field) RETURN count(r) AS count")[0]["count"]

    visual_paths = run_neo4j(
        """
        MATCH (input:Field)-[:IS_INPUT_OF]->(dpi:DataProcessingItem)<-[:IS_OUTPUT_OF]-(output:Field)
        OPTIONAL MATCH (dpi)-[:PART_OF]->(dp:DataProcessing)
        RETURN
            count(*) AS visual_paths,
            count(DISTINCT dpi) AS distinct_dpi,
            count(DISTINCT dp) AS distinct_dp
        """
    )[0]

    return {
        "data_processing_count": int(dp or 0),
        "data_processing_item_count": int(dpi or 0),
        "part_of_relationships": int(part_of or 0),
        "flows_to_relationships": int(flows_to or 0),
        "visual_paths": {
            "visual_paths": int(visual_paths["visual_paths"] or 0),
            "distinct_dpi": int(visual_paths["distinct_dpi"] or 0),
            "distinct_dp": int(visual_paths["distinct_dp"] or 0),
        },
    }


def main() -> None:
    print("=" * 80)
    print("TARGETED DP/DPI MODEL REPAIR")
    print("=" * 80)

    ensure_indexes()

    available_tables = existing_pg_tables()
    link_table = resolve_table("link", available_tables)

    if link_table is None:
        raise RuntimeError("Could not find link or dg_link table.")

    print(f"[INFO] Link table: {link_table}")

    processing_rows = load_processing_nodes_from_postgres(link_table)

    merge_summary = merge_processing_nodes(processing_rows)
    part_of_summary = create_part_of_relationships()
    flows_to_summary = create_flows_to_relationships()
    post_stats = print_post_repair_stats()

    final_summary = {
        "link_table": link_table,
        "merge_summary": merge_summary,
        "part_of_summary": part_of_summary,
        "flows_to_summary": flows_to_summary,
        "post_repair_stats": post_stats,
        "output_dir": str(OUTPUT_DIR),
    }

    summary_file = OUTPUT_DIR / "dp_dpi_repair_summary.json"
    summary_file.write_text(
        json.dumps(final_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 80)
    print("DP/DPI REPAIR COMPLETE")
    print("=" * 80)
    print(json.dumps(final_summary, indent=2, ensure_ascii=False))

    neo4j.close()


if __name__ == "__main__":
    main()