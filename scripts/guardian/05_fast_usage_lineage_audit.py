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
        "AUDIT_OUTPUT_DIR",
        "reports/migration_guardian/fast_usage_lineage_audit",
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

BROKEN_REL_MAPPING = {
    "ISLINKEDTO": "IS_LINKED_TO",
    "HASFORUNIVERSE": "HAS_FOR_UNIVERSE",
    "ISUNIVERSEOF": "IS_UNIVERSE_OF",
    "ISCALLEDBY": "IS_CALLED_BY",
    "ISIMPLEMENTEDBY": "IS_IMPLEMENTED_BY",
    "ISUSEDFORCOMPUTATIONOF": "IS_USED_FOR_COMPUTATION_OF",
    "ISPARTOFDIMENSION": "IS_PART_OF_DIMENSION",
    "HASFORSOURCE": "HAS_FOR_SOURCE",
    "ISSOURCEOF": "IS_SOURCE_OF",
}

USAGE_RELEVANT_RELS = {
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

    upper = text.upper().strip()
    if upper in BROKEN_REL_MAPPING:
        return BROKEN_REL_MAPPING[upper]

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


# =============================================================================
# LOAD USAGE-LIKE RELATIONSHIPS
# =============================================================================

def load_usage_like_link_rows(link_table: str) -> list[dict[str, Any]]:
    print("[PG] Loading usage-like link rows...")

    rows = fetch_pg(
        f"""
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
        row["expected_relationship"] = rel

        src_usage = endpoint_is_usage_like(row, "src")
        tgt_usage = endpoint_is_usage_like(row, "tgt")

        row["src_is_usage_like"] = src_usage
        row["tgt_is_usage_like"] = tgt_usage

        if src_usage or tgt_usage or rel in USAGE_RELEVANT_RELS:
            selected.append(row)

    print(f"[PG] Total link rows: {len(rows):,}")
    print(f"[PG] Usage-like / usage-relevant rows selected: {len(selected):,}")

    print("[PG] Relationship distribution:")
    for rel, count in Counter(row["expected_relationship"] for row in selected).most_common():
        print(f"  {rel:<35} {count:,}")

    return selected


def build_expected_usage_nodes(link_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}

    for row in link_rows:
        for side in ("src", "tgt"):
            node_id = row.get(f"{side}_node_id")
            if not node_id:
                continue

            is_usage = bool(row.get(f"{side}_is_usage_like"))

            if not is_usage:
                continue

            nodes[str(node_id)] = {
                "node_id": str(node_id),
                "name_label": row.get(f"{side}_name_label"),
                "name_tech": row.get(f"{side}_name_tech"),
                "entity_type": row.get(f"{side}_entity_type"),
                "data_type": row.get(f"{side}_data_type"),
                "path_type": row.get(f"{side}_path_type"),
                "path_full": row.get(f"{side}_path"),
            }

    return list(nodes.values())


# =============================================================================
# AUDIT NODE PRESENCE
# =============================================================================

def audit_usage_nodes(expected_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    print("[AUDIT] Usage-like node presence...")

    output_file = OUTPUT_DIR / "missing_usage_like_nodes.csv"

    query = """
    UNWIND $rows AS row

    OPTIONAL MATCH (dg:DataGalaxyObject {node_id: row.node_id})
    OPTIONAL MATCH (u1:Usage {usage_uuid: row.node_id})
    OPTIONAL MATCH (u2:Usage {node_id: row.node_id})

    WITH row, coalesce(dg, u1, u2) AS n

    RETURN
        row.node_id AS node_id,
        row.name_label AS postgres_name_label,
        row.name_tech AS postgres_name_tech,
        row.entity_type AS postgres_entity_type,
        row.data_type AS postgres_data_type,
        row.path_type AS postgres_path_type,
        row.path_full AS postgres_path_full,

        n IS NOT NULL AS exists_in_neo4j,
        labels(n) AS neo4j_labels,
        n.node_id AS neo4j_node_id,
        n.usage_uuid AS neo4j_usage_uuid,
        n.name_label AS neo4j_name_label,
        n.name_tech AS neo4j_name_tech,
        n.usage_name AS neo4j_usage_name
    """

    checked = 0
    missing = 0

    with output_file.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "status",
            "node_id",
            "postgres_name_label",
            "postgres_name_tech",
            "postgres_entity_type",
            "postgres_data_type",
            "postgres_path_type",
            "postgres_path_full",
            "neo4j_labels",
            "neo4j_node_id",
            "neo4j_usage_uuid",
            "neo4j_name_label",
            "neo4j_name_tech",
            "neo4j_usage_name",
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for batch_index, batch in enumerate(chunks(expected_nodes, BATCH_SIZE), start=1):
            rows = run_neo4j(query, {"rows": batch})

            for row in rows:
                checked += 1
                row["neo4j_labels"] = "|".join(row.get("neo4j_labels") or [])

                if not row["exists_in_neo4j"]:
                    missing += 1
                    row["status"] = "MISSING_USAGE_LIKE_NODE"
                    writer.writerow({k: row.get(k) for k in fieldnames})

            print(
                f"  batch {batch_index:<5} checked={checked:,}/{len(expected_nodes):,} "
                f"missing={missing:,}",
                flush=True,
            )

    return {
        "expected_usage_like_nodes": len(expected_nodes),
        "checked": checked,
        "missing_usage_like_nodes": missing,
        "missing_nodes_file": str(output_file),
        "status": "OK" if missing == 0 else "MISMATCH",
    }


# =============================================================================
# AUDIT RELATIONSHIPS
# =============================================================================

def audit_usage_relationships(link_rows: list[dict[str, Any]]) -> dict[str, Any]:
    print("[AUDIT] Usage-like / usage-relevant relationships...")

    output_file = OUTPUT_DIR / "bad_usage_like_relationships.csv"

    query = """
    UNWIND $rows AS row

    OPTIONAL MATCH (src_dg:DataGalaxyObject {node_id: row.src_node_id})
    OPTIONAL MATCH (src_usage_uuid:Usage {usage_uuid: row.src_node_id})
    OPTIONAL MATCH (src_usage_node_id:Usage {node_id: row.src_node_id})

    OPTIONAL MATCH (tgt_dg:DataGalaxyObject {node_id: row.tgt_node_id})
    OPTIONAL MATCH (tgt_usage_uuid:Usage {usage_uuid: row.tgt_node_id})
    OPTIONAL MATCH (tgt_usage_node_id:Usage {node_id: row.tgt_node_id})

    WITH row,
         coalesce(src_dg, src_usage_uuid, src_usage_node_id) AS src,
         coalesce(tgt_dg, tgt_usage_uuid, tgt_usage_node_id) AS tgt

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
        row.tgt_path_type AS tgt_path_type,
        row.tgt_path AS tgt_path,

        src IS NOT NULL AS src_exists,
        tgt IS NOT NULL AS tgt_exists,
        labels(src) AS src_labels,
        labels(tgt) AS tgt_labels,
        r IS NOT NULL AS relationship_exists
    """

    checked = 0
    ok = 0
    missing_src = 0
    missing_tgt = 0
    missing_rel = 0

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
            "tgt_path_type",
            "tgt_path",
            "src_labels",
            "tgt_labels",
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for batch_index, batch in enumerate(chunks(link_rows, BATCH_SIZE), start=1):
            rows = run_neo4j(query, {"rows": batch})

            for row in rows:
                checked += 1
                row["src_labels"] = "|".join(row.get("src_labels") or [])
                row["tgt_labels"] = "|".join(row.get("tgt_labels") or [])

                if not row["src_exists"]:
                    row["status"] = "SOURCE_NODE_MISSING"
                    missing_src += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                elif not row["tgt_exists"]:
                    row["status"] = "TARGET_NODE_MISSING"
                    missing_tgt += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                elif not row["relationship_exists"]:
                    row["status"] = "RELATIONSHIP_MISSING"
                    missing_rel += 1
                    writer.writerow({k: row.get(k) for k in fieldnames})

                else:
                    ok += 1

            print(
                f"  batch {batch_index:<5} checked={checked:,}/{len(link_rows):,} "
                f"ok={ok:,} missing_src={missing_src:,} "
                f"missing_tgt={missing_tgt:,} missing_rel={missing_rel:,}",
                flush=True,
            )

    return {
        "expected_usage_like_relationships": len(link_rows),
        "checked": checked,
        "ok": ok,
        "missing_source": missing_src,
        "missing_target": missing_tgt,
        "missing_relationship": missing_rel,
        "bad_relationships_file": str(output_file),
        "status": "OK"
        if checked == ok and missing_src == 0 and missing_tgt == 0 and missing_rel == 0
        else "MISMATCH",
    }


# =============================================================================
# EXTRA REPORT
# =============================================================================

def relationship_distribution_in_neo4j() -> list[dict[str, Any]]:
    return run_neo4j(
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


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("=" * 80)
    print("FAST USAGE LINEAGE AUDIT")
    print("=" * 80)
    print(f"PostgreSQL: {POSTGRES_URL}")
    print(f"Neo4j:      {NEO4J_URI}")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Output:     {OUTPUT_DIR}")
    print("=" * 80)

    available_tables = existing_pg_tables()
    link_table = resolve_table("link", available_tables)

    if link_table is None:
        raise RuntimeError("Could not find link or dg_link table.")

    print(f"[INFO] Link table: {link_table}")

    link_rows = load_usage_like_link_rows(link_table)
    expected_usage_nodes = build_expected_usage_nodes(link_rows)

    node_summary = audit_usage_nodes(expected_usage_nodes)
    relationship_summary = audit_usage_relationships(link_rows)
    neo4j_distribution = relationship_distribution_in_neo4j()

    final_summary = {
        "link_table": link_table,
        "usage_node_summary": node_summary,
        "usage_relationship_summary": relationship_summary,
        "relationship_type_distribution_from_postgres": dict(
            Counter(row["expected_relationship"] for row in link_rows)
        ),
        "relationship_type_distribution_in_neo4j": neo4j_distribution,
        "output_dir": str(OUTPUT_DIR),
    }

    summary_file = OUTPUT_DIR / "fast_usage_lineage_audit_summary.json"
    summary_file.write_text(
        json.dumps(final_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 80)
    print("FAST USAGE LINEAGE AUDIT COMPLETE")
    print("=" * 80)
    print(json.dumps(final_summary, indent=2, ensure_ascii=False))

    neo4j.close()


if __name__ == "__main__":
    main()