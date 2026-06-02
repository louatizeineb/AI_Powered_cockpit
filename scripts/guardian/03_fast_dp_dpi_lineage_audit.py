from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
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
        "AUDIT_OUTPUT_DIR",
        "reports/migration_guardian/fast_dp_dpi_lineage_audit",
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


REL_MAPPING = {
    "IsInputOf": "IS_INPUT_OF",
    "IsOutputOf": "IS_OUTPUT_OF",
}


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


def parent_path_of_dpi(path: Any) -> str | None:
    normalized = normalize_path(path)
    if not normalized:
        return None

    parts = normalized.split("\\")
    if len(parts) <= 1:
        return None

    return "\\".join(parts[:-1])


def normalize_rel_type(link_type: Any) -> str | None:
    if link_type is None:
        return None

    text = str(link_type).strip()

    return REL_MAPPING.get(text)


def fetch_pg(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with pg.connect() as conn:
        result = conn.execute(text(query), params or {})
        return [
            {key: clean_value(value) for key, value in row.items()}
            for row in result.mappings().all()
        ]


def stream_pg(query: str, params: dict[str, Any] | None = None):
    with pg.connect().execution_options(stream_results=True) as conn:
        result = conn.execute(text(query), params or {})
        for row in result.mappings():
            yield {key: clean_value(value) for key, value in row.items()}


def run_neo4j(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with neo4j.session() as session:
        result = session.run(query, **(params or {}))
        rows = [dict(record) for record in result]
        result.consume()
        return rows


def chunks(iterator: Iterable[dict[str, Any]], size: int):
    batch = []
    for item in iterator:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


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
    print("[NEO4J] Ensuring lineage indexes...")

    queries = [
        """
        CREATE CONSTRAINT field_node_id IF NOT EXISTS
        FOR (n:Field)
        REQUIRE n.node_id IS UNIQUE
        """,
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
        CREATE INDEX dp_path IF NOT EXISTS
        FOR (n:DataProcessing)
        ON (n.normalized_path)
        """,
        """
        CREATE INDEX dpi_path IF NOT EXISTS
        FOR (n:DataProcessingItem)
        ON (n.normalized_path)
        """,
    ]

    for q in queries:
        run_neo4j(q)


def load_expected_processing_nodes(link_table: str) -> list[dict[str, Any]]:
    print("[PG] Loading expected DP/DPI nodes from link table...")

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
        row["expected_label"] = (
            "DataProcessingItem"
            if row.get("entity_type") == "DataProcessingItem"
            or row.get("data_type") == "DataProcessingItem"
            else "DataProcessing"
        )
        row["normalized_path"] = normalize_path(row.get("path_full"))
        row["expected_parent_dp_path"] = (
            parent_path_of_dpi(row.get("path_full"))
            if row["expected_label"] == "DataProcessingItem"
            else None
        )

    return rows


def audit_processing_node_presence(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    print("[AUDIT] DP/DPI node presence...")

    output_file = OUTPUT_DIR / "missing_processing_nodes.csv"

    query = """
    UNWIND $rows AS row

    OPTIONAL MATCH (dp:DataProcessing {node_id: row.node_id})
    OPTIONAL MATCH (dpi:DataProcessingItem {node_id: row.node_id})

    WITH row, dp, dpi,
         CASE
            WHEN row.expected_label = 'DataProcessing' THEN dp
            WHEN row.expected_label = 'DataProcessingItem' THEN dpi
            ELSE NULL
         END AS n

    RETURN
        row.node_id AS node_id,
        row.expected_label AS expected_label,
        row.name_label AS postgres_name_label,
        row.name_tech AS postgres_name_tech,
        row.entity_type AS postgres_entity_type,
        row.data_type AS postgres_data_type,
        row.path_full AS postgres_path_full,
        row.normalized_path AS postgres_normalized_path,
        n IS NOT NULL AS exists_in_neo4j,
        labels(n) AS neo4j_labels,
        n.name_label AS neo4j_name_label,
        n.name_tech AS neo4j_name_tech,
        n.path_full AS neo4j_path_full,
        n.normalized_path AS neo4j_normalized_path
    """

    total = len(nodes)
    checked = 0
    missing = 0
    by_type = Counter()

    with output_file.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "status",
            "node_id",
            "expected_label",
            "postgres_name_label",
            "postgres_name_tech",
            "postgres_entity_type",
            "postgres_data_type",
            "postgres_path_full",
            "postgres_normalized_path",
            "neo4j_labels",
            "neo4j_name_label",
            "neo4j_name_tech",
            "neo4j_path_full",
            "neo4j_normalized_path",
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for batch_index, batch in enumerate(chunks(iter(nodes), BATCH_SIZE), start=1):
            rows = run_neo4j(query, {"rows": batch})

            for row in rows:
                checked += 1
                by_type[row["expected_label"]] += 1

                if not row["exists_in_neo4j"]:
                    missing += 1
                    row["status"] = "MISSING_PROCESSING_NODE"
                    row["neo4j_labels"] = "|".join(row.get("neo4j_labels") or [])
                    writer.writerow({k: row.get(k) for k in fieldnames})

            print(
                f"  batch {batch_index:<5} checked={checked:,}/{total:,} "
                f"missing={missing:,}",
                flush=True,
            )

    return {
        "expected_processing_nodes": total,
        "expected_by_type": dict(by_type),
        "missing_processing_nodes": missing,
        "missing_nodes_file": str(output_file),
        "status": "OK" if missing == 0 else "MISMATCH",
    }


def audit_field_to_processing_relationships(link_table: str) -> dict[str, Any]:
    print("[AUDIT] Field -> DP/DPI IS_INPUT_OF / IS_OUTPUT_OF relationships...")

    output_file = OUTPUT_DIR / "bad_field_to_processing_relationships.csv"

    pg_query = f"""
        SELECT
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
            tgt_path
        FROM {link_table}
        WHERE src_node_id IS NOT NULL
          AND tgt_node_id IS NOT NULL
          AND link_type IN ('IsInputOf', 'IsOutputOf')
          AND (
                tgt_entity_type IN ('DataProcessing', 'DataProcessingItem')
             OR tgt_data_type IN ('DataProcessing', 'DataProcessingItem')
          )
    """

    count_row = fetch_pg(
        f"""
        SELECT COUNT(*) AS count
        FROM {link_table}
        WHERE src_node_id IS NOT NULL
          AND tgt_node_id IS NOT NULL
          AND link_type IN ('IsInputOf', 'IsOutputOf')
          AND (
                tgt_entity_type IN ('DataProcessing', 'DataProcessingItem')
             OR tgt_data_type IN ('DataProcessing', 'DataProcessingItem')
          )
        """
    )[0]

    expected_count = int(count_row["count"])

    # Uses label-specific indexed lookup for Field.
    # Target may be DP or DPI.
    query = """
    UNWIND $rows AS row

    OPTIONAL MATCH (src:Field {node_id: row.src_node_id})
    OPTIONAL MATCH (dp:DataProcessing {node_id: row.tgt_node_id})
    OPTIONAL MATCH (dpi:DataProcessingItem {node_id: row.tgt_node_id})

    WITH row, src, coalesce(dp, dpi) AS tgt

    OPTIONAL MATCH (src)-[r]->(tgt)
    WHERE type(r) = row.expected_relationship

    RETURN
        row.src_node_id AS src_node_id,
        row.src_name_label AS src_name_label,
        row.src_name_tech AS src_name_tech,
        row.src_entity_type AS src_entity_type,
        row.src_data_type AS src_data_type,
        row.link_type AS postgres_link_type,
        row.expected_relationship AS expected_relationship,
        row.tgt_node_id AS tgt_node_id,
        row.tgt_name_label AS tgt_name_label,
        row.tgt_name_tech AS tgt_name_tech,
        row.tgt_entity_type AS tgt_entity_type,
        row.tgt_data_type AS tgt_data_type,
        row.tgt_path AS tgt_path,

        src IS NOT NULL AS src_field_exists,
        tgt IS NOT NULL AS target_processing_exists,
        labels(tgt) AS target_labels,
        r IS NOT NULL AS relationship_exists
    """

    checked = 0
    ok = 0
    missing_src = 0
    missing_target = 0
    missing_relationship = 0

    with output_file.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "status",
            "src_node_id",
            "src_name_label",
            "src_name_tech",
            "src_entity_type",
            "src_data_type",
            "postgres_link_type",
            "expected_relationship",
            "tgt_node_id",
            "tgt_name_label",
            "tgt_name_tech",
            "tgt_entity_type",
            "tgt_data_type",
            "tgt_path",
            "target_labels",
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        stream = stream_pg(pg_query)

        for batch_index, batch in enumerate(chunks(stream, BATCH_SIZE), start=1):
            for row in batch:
                row["expected_relationship"] = normalize_rel_type(row["link_type"])

            rows = run_neo4j(query, {"rows": batch})

            for row in rows:
                checked += 1
                row["target_labels"] = "|".join(row.get("target_labels") or [])

                if not row["src_field_exists"]:
                    row["status"] = "SOURCE_FIELD_MISSING"
                    missing_src += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                elif not row["target_processing_exists"]:
                    row["status"] = "TARGET_PROCESSING_NODE_MISSING"
                    missing_target += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                elif not row["relationship_exists"]:
                    row["status"] = "RELATIONSHIP_MISSING"
                    missing_relationship += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                else:
                    ok += 1

            print(
                f"  batch {batch_index:<5} checked={checked:,}/{expected_count:,} "
                f"ok={ok:,} missing_rel={missing_relationship:,} "
                f"missing_target={missing_target:,}",
                flush=True,
            )

    return {
        "expected_field_to_processing_relationships": expected_count,
        "checked": checked,
        "ok": ok,
        "missing_source_field": missing_src,
        "missing_target_processing_node": missing_target,
        "missing_relationship": missing_relationship,
        "bad_relationships_file": str(output_file),
        "status": "OK"
        if checked == ok and missing_src == 0 and missing_target == 0 and missing_relationship == 0
        else "MISMATCH",
    }


def audit_dpi_part_of_dp(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    print("[AUDIT] DPI -> DP PART_OF relationships...")

    dpi_rows = [
        row
        for row in nodes
        if row["expected_label"] == "DataProcessingItem"
    ]

    output_file = OUTPUT_DIR / "bad_dpi_part_of_dp.csv"

    query = """
    UNWIND $rows AS row

    OPTIONAL MATCH (dpi:DataProcessingItem {node_id: row.node_id})
    OPTIONAL MATCH (dp:DataProcessing {normalized_path: row.expected_parent_dp_path})
    OPTIONAL MATCH (dpi)-[r:PART_OF]->(dp)

    RETURN
        row.node_id AS dpi_node_id,
        row.name_label AS dpi_name_label,
        row.name_tech AS dpi_name_tech,
        row.path_full AS dpi_path_full,
        row.normalized_path AS dpi_normalized_path,
        row.expected_parent_dp_path AS expected_parent_dp_path,

        dpi IS NOT NULL AS dpi_exists,
        dp IS NOT NULL AS expected_dp_exists,
        dp.node_id AS dp_node_id,
        dp.name_label AS dp_name_label,
        dp.name_tech AS dp_name_tech,
        r IS NOT NULL AS part_of_exists
    """

    checked = 0
    ok = 0
    missing_dpi = 0
    missing_dp = 0
    missing_part_of = 0

    with output_file.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "status",
            "dpi_node_id",
            "dpi_name_label",
            "dpi_name_tech",
            "dpi_path_full",
            "dpi_normalized_path",
            "expected_parent_dp_path",
            "dp_node_id",
            "dp_name_label",
            "dp_name_tech",
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for batch_index, batch in enumerate(chunks(iter(dpi_rows), BATCH_SIZE), start=1):
            rows = run_neo4j(query, {"rows": batch})

            for row in rows:
                checked += 1

                if not row["dpi_exists"]:
                    row["status"] = "DPI_MISSING"
                    missing_dpi += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                elif not row["expected_parent_dp_path"]:
                    row["status"] = "NO_PARENT_PATH_DERIVABLE"
                    missing_dp += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                elif not row["expected_dp_exists"]:
                    row["status"] = "PARENT_DP_MISSING_BY_PATH"
                    missing_dp += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                elif not row["part_of_exists"]:
                    row["status"] = "PART_OF_MISSING"
                    missing_part_of += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                else:
                    ok += 1

            print(
                f"  batch {batch_index:<5} checked={checked:,}/{len(dpi_rows):,} "
                f"ok={ok:,} missing_dp={missing_dp:,} missing_part_of={missing_part_of:,}",
                flush=True,
            )

    return {
        "expected_dpi_nodes": len(dpi_rows),
        "checked": checked,
        "ok": ok,
        "missing_dpi": missing_dpi,
        "missing_parent_dp_by_path": missing_dp,
        "missing_part_of": missing_part_of,
        "bad_part_of_file": str(output_file),
        "status": "OK"
        if checked == ok and missing_dpi == 0 and missing_dp == 0 and missing_part_of == 0
        else "MISMATCH",
    }


def audit_derived_visual_lineage_capacity() -> dict[str, Any]:
    print("[AUDIT] Derived Field -> DPI -> Field visual lineage capacity...")

    query = """
    MATCH (input:Field)-[:IS_INPUT_OF]->(dpi:DataProcessingItem)<-[:IS_OUTPUT_OF]-(output:Field)
    OPTIONAL MATCH (dpi)-[:PART_OF]->(dp:DataProcessing)
    RETURN
        count(*) AS visual_paths,
        count(DISTINCT input) AS distinct_input_fields,
        count(DISTINCT output) AS distinct_output_fields,
        count(DISTINCT dpi) AS distinct_dpi_nodes,
        count(DISTINCT dp) AS distinct_dp_nodes
    """

    result = run_neo4j(query)[0]

    sample_query = """
    MATCH (input:Field)-[:IS_INPUT_OF]->(dpi:DataProcessingItem)<-[:IS_OUTPUT_OF]-(output:Field)
    OPTIONAL MATCH (dpi)-[:PART_OF]->(dp:DataProcessing)
    RETURN
        input.node_id AS input_field_id,
        input.name_tech AS input_field_name,
        dpi.node_id AS dpi_id,
        dpi.name_tech AS dpi_name,
        dp.node_id AS dp_id,
        dp.name_tech AS dp_name,
        output.node_id AS output_field_id,
        output.name_tech AS output_field_name
    LIMIT 100
    """

    samples = run_neo4j(sample_query)

    sample_file = OUTPUT_DIR / "visual_lineage_samples.csv"

    with sample_file.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "input_field_id",
            "input_field_name",
            "dpi_id",
            "dpi_name",
            "dp_id",
            "dp_name",
            "output_field_id",
            "output_field_name",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(samples)

    result["sample_file"] = str(sample_file)
    result["status"] = "OK" if int(result["visual_paths"] or 0) > 0 else "NO_VISUAL_PATHS_DERIVED"

    return result


def main() -> None:
    print("=" * 80)
    print("FAST DP/DPI LINEAGE AUDIT")
    print("=" * 80)

    ensure_indexes()

    available_tables = existing_pg_tables()
    link_table = resolve_table("link", available_tables)

    if link_table is None:
        raise RuntimeError("Could not find link or dg_link table.")

    print(f"[INFO] Link table: {link_table}")

    processing_nodes = load_expected_processing_nodes(link_table)

    processing_node_summary = audit_processing_node_presence(processing_nodes)
    field_to_processing_summary = audit_field_to_processing_relationships(link_table)
    dpi_part_of_summary = audit_dpi_part_of_dp(processing_nodes)
    visual_capacity_summary = audit_derived_visual_lineage_capacity()

    final_summary = {
        "link_table": link_table,
        "processing_node_summary": processing_node_summary,
        "field_to_processing_summary": field_to_processing_summary,
        "dpi_part_of_summary": dpi_part_of_summary,
        "visual_capacity_summary": visual_capacity_summary,
        "output_dir": str(OUTPUT_DIR),
    }

    summary_file = OUTPUT_DIR / "fast_dp_dpi_lineage_audit_summary.json"
    summary_file.write_text(
        json.dumps(final_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 80)
    print("FAST DP/DPI LINEAGE AUDIT COMPLETE")
    print("=" * 80)
    print(json.dumps(final_summary, indent=2, ensure_ascii=False))

    neo4j.close()


if __name__ == "__main__":
    main()