from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .graph_auditor import compute_hierarchy_metadata, object_types_for_node, primary_object_type


TECHNICAL_RELATIONSHIP_TYPES = {
    "IS_INPUT_OF",
    "IS_OUTPUT_OF",
    "FLOWS_TO",
    "USES",
    "IS_USED_BY",
    "CALLS",
    "IS_CALLED_BY",
    "IS_USED_FOR_COMPUTATION_OF",
    "IS_USAGE_SOURCE_FOR",
    "IS_USAGE_DESTINATION_FOR",
    "HAS_FOR_SOURCE",
    "IS_SOURCE_OF",
    "RESOLVED_TO_SOURCE",
    "PART_OF",
}


def chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def stable_path_hash(export_id: str, family: str, start_node_id: str, end_node_id: str, nodes: list[Any], rels: list[Any]) -> str:
    payload = {
        "export_id": export_id,
        "family": family,
        "start_node_id": start_node_id,
        "end_node_id": end_node_id,
        "nodes": nodes,
        "relationships": rels,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def fetch_objects(engine: Engine, export_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    node_id,
                    parent_node_id,
                    array_agg(DISTINCT object_type ORDER BY object_type) AS object_types,
                    min(object_type) AS object_type,
                    min(name_label) AS name_label,
                    min(name_tech) AS name_tech,
                    min(path_full) AS path_full,
                    min(status) AS status
                FROM migration_trusted_object_projection
                WHERE export_id = :export_id
                GROUP BY node_id, parent_node_id
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
    return [dict(row) for row in rows]


def fetch_relationships(engine: Engine, export_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, src_node_id, tgt_node_id, relationship_type, relationship_family, link_type, source_table, status
                FROM migration_trusted_relationship_projection
                WHERE export_id = :export_id
                ORDER BY id
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
    return [dict(row) for row in rows]


def node_summary(row: dict[str, Any]) -> dict[str, Any]:
    types = object_types_for_node(row)
    return {
        "node_id": row["node_id"],
        "object_type": primary_object_type(types),
        "object_types": types,
        "name_label": row.get("name_label"),
        "name_tech": row.get("name_tech"),
        "path_full": row.get("path_full"),
        "status": row.get("status"),
    }


def build_catalog_hierarchy_paths(
    export_id: str,
    objects: list[dict[str, Any]],
    max_paths: int | None = None,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    by_node: dict[str, dict[str, Any]] = {}
    parent_by_node: dict[str, str | None] = {}
    for row in objects:
        node_id = str(row["node_id"])
        existing = by_node.setdefault(node_id, dict(row))
        if not existing.get("parent_node_id") and row.get("parent_node_id"):
            existing["parent_node_id"] = row.get("parent_node_id")
        existing["object_types"] = sorted(set(object_types_for_node(existing)) | set(object_types_for_node(row)))

    for node_id, row in by_node.items():
        parent_by_node[node_id] = str(row["parent_node_id"]) if row.get("parent_node_id") else None

    metadata = compute_hierarchy_metadata(list(by_node.values()))
    rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    for node_id, row in by_node.items():
        if max_paths is not None and len(rows) >= max_paths:
            counters["catalog_hierarchy_skipped_by_limit"] += 1
            continue
        chain_ids: list[str] = []
        cursor: str | None = node_id
        seen: set[str] = set()
        while cursor:
            if cursor in seen:
                counters["catalog_hierarchy_skipped_cycle"] += 1
                chain_ids = []
                break
            seen.add(cursor)
            chain_ids.append(cursor)
            parent_id = parent_by_node.get(cursor)
            if not parent_id:
                break
            if parent_id not in by_node:
                counters["catalog_hierarchy_partial_missing_parent"] += 1
                break
            cursor = parent_id
        if len(chain_ids) < 2:
            continue
        chain_ids.reverse()
        nodes = [node_summary(by_node[item]) for item in chain_ids if item in by_node]
        rels = [
            {
                "source": chain_ids[index],
                "target": chain_ids[index + 1],
                "type": "HAS_FIELD" if primary_object_type(object_types_for_node(by_node[chain_ids[index + 1]])) == "Field" else "CONTAINS",
            }
            for index in range(len(chain_ids) - 1)
        ]
        family = "catalog_hierarchy"
        rows.append(
            {
                "export_id": export_id,
                "graph_version": None,
                "start_node_id": chain_ids[0],
                "end_node_id": chain_ids[-1],
                "path_hash": stable_path_hash(export_id, family, chain_ids[0], chain_ids[-1], nodes, rels),
                "path_nodes": nodes,
                "path_relationships": rels,
                "path_length": len(rels),
                "path_family": family,
                "evidence": {
                    "source": "catalog_object_staging.parent_node_id",
                    "root_source_id": metadata.get(node_id, {}).get("root_source_id"),
                    "hierarchy_depth": metadata.get(node_id, {}).get("hierarchy_depth"),
                },
            }
        )
        counters[family] += 1
    return rows, counters


def relationship_path_row(
    export_id: str,
    family: str,
    start_node_id: str,
    end_node_id: str,
    relationship_type: str,
    row: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    nodes = [
        node_summary(node_by_id.get(start_node_id, {"node_id": start_node_id, "object_types": []})),
        node_summary(node_by_id.get(end_node_id, {"node_id": end_node_id, "object_types": []})),
    ]
    rels = [
        {
            "source": start_node_id,
            "target": end_node_id,
            "type": relationship_type,
            "staging_id": row.get("id"),
            "link_type": row.get("link_type"),
        }
    ]
    return {
        "export_id": export_id,
        "graph_version": None,
        "start_node_id": start_node_id,
        "end_node_id": end_node_id,
        "path_hash": stable_path_hash(export_id, family, start_node_id, end_node_id, nodes, rels),
        "path_nodes": nodes,
        "path_relationships": rels,
        "path_length": 1,
        "path_family": family,
        "evidence": {
            "source": "catalog_relationship_staging",
            "staging_id": row.get("id"),
            "relationship_family": row.get("relationship_family"),
            "link_type": row.get("link_type"),
            "source_table": row.get("source_table"),
            "status": row.get("status"),
        },
    }


def build_relationship_paths(
    export_id: str,
    objects: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    max_paths_per_family: int | None = None,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    node_by_id = {str(row["node_id"]): row for row in objects}
    object_type_by_id = {
        node_id: primary_object_type(object_types_for_node(row))
        for node_id, row in node_by_id.items()
    }
    rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()

    def under_limit(family: str) -> bool:
        return max_paths_per_family is None or counters[family] < max_paths_per_family

    for row in relationships:
        src = str(row["src_node_id"])
        tgt = str(row["tgt_node_id"])
        rel_type = str(row.get("relationship_type") or "IS_LINKED_TO").upper()
        src_type = object_type_by_id.get(src)
        tgt_type = object_type_by_id.get(tgt)

        if rel_type == "IMPLEMENTS" and under_limit("semantic_implements"):
            rows.append(relationship_path_row(export_id, "semantic_implements", src, tgt, rel_type, row, node_by_id))
            counters["semantic_implements"] += 1

        if (src_type == "Usage" or tgt_type == "Usage" or "USAGE" in rel_type) and under_limit("usage_context"):
            rows.append(relationship_path_row(export_id, "usage_context", src, tgt, rel_type, row, node_by_id))
            counters["usage_context"] += 1

        if rel_type in TECHNICAL_RELATIONSHIP_TYPES:
            if under_limit("technical_downstream"):
                rows.append(relationship_path_row(export_id, "technical_downstream", src, tgt, rel_type, row, node_by_id))
                counters["technical_downstream"] += 1
            if under_limit("technical_upstream"):
                rows.append(relationship_path_row(export_id, "technical_upstream", tgt, src, rel_type, row, node_by_id))
                counters["technical_upstream"] += 1
    return rows, counters


def insert_paths(engine: Engine, export_id: str, rows: list[dict[str, Any]], batch_size: int = 1000) -> int:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM lineage_path WHERE export_id = :export_id"), {"export_id": export_id})
        total = 0
        insert_sql = text(
            """
            INSERT INTO lineage_path(
                export_id, graph_version, start_node_id, end_node_id, path_hash,
                path_nodes, path_relationships, path_length, path_family, evidence
            )
            VALUES (
                :export_id, :graph_version, :start_node_id, :end_node_id, :path_hash,
                CAST(:path_nodes AS jsonb), CAST(:path_relationships AS jsonb),
                :path_length, :path_family, CAST(:evidence AS jsonb)
            )
            ON CONFLICT DO NOTHING
            """
        )
        for batch in chunks(rows, batch_size):
            params = [
                {
                    **row,
                    "path_nodes": json.dumps(row["path_nodes"], default=str),
                    "path_relationships": json.dumps(row["path_relationships"], default=str),
                    "evidence": json.dumps(row["evidence"], default=str),
                }
                for row in batch
            ]
            conn.execute(insert_sql, params)
            total += len(batch)
    return total


def build_lineage_paths(
    export_id: str,
    engine: Engine,
    batch_size: int = 1000,
    max_paths_per_family: int | None = None,
) -> dict[str, Any]:
    objects = fetch_objects(engine, export_id)
    relationships = fetch_relationships(engine, export_id)
    catalog_rows, catalog_counts = build_catalog_hierarchy_paths(export_id, objects, max_paths_per_family)
    relationship_rows, relationship_counts = build_relationship_paths(
        export_id,
        objects,
        relationships,
        max_paths_per_family=max_paths_per_family,
    )
    all_rows = [*catalog_rows, *relationship_rows]
    inserted = insert_paths(engine, export_id, all_rows, batch_size=batch_size)
    counts = Counter()
    counts.update(catalog_counts)
    counts.update(relationship_counts)
    return {
        "export_id": export_id,
        "status": "completed",
        "objects_read": len(objects),
        "relationships_read": len(relationships),
        "paths_generated": len(all_rows),
        "paths_inserted_attempted": inserted,
        "path_family_counts": dict(sorted(counts.items())),
        "max_paths_per_family": max_paths_per_family,
    }
