from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Iterable

from sqlalchemy import text

from _common import (
    config_section,
    ensure_tables,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)
from app.migration_v2.graph.graph_auditor import (
    classify_hierarchy_edge,
    compute_hierarchy_metadata,
    object_types_for_node,
    primary_object_type,
)
from app.migration_v2.graph.usage_relationship_resolver import resolve_usage_relationships


LOGGER = setup_logging("migration_v2.build_graph")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a candidate Neo4j graph from approved migration_v2 staging.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    parser.add_argument("--batch-size", type=int, default=1000, help="Neo4j write batch size.")
    parser.add_argument("--dry-run", action="store_true", help="Validate readiness without writing Neo4j.")
    parser.add_argument("--force", action="store_true", help="Build even when validation errors are open.")
    parser.add_argument(
        "--clear-first",
        action="store_true",
        help="Delete all nodes and relationships in the target Neo4j database before loading.",
    )
    parser.add_argument(
        "--clear-batch-size",
        type=int,
        default=5000,
        help="Batch size for --clear-first deletion to avoid Neo4j transaction memory pressure.",
    )
    parser.add_argument(
        "--skip-usage-resolver",
        action="store_true",
        help="Skip deterministic Usage -> catalog relationship resolution.",
    )
    parser.add_argument(
        "--usage-dataset-path-limit",
        type=int,
        default=0,
        help="Per-usage cap for dataset_ref path-contains matches. 0 keeps only app_code and exact dataset_ref matches.",
    )
    return parser.parse_args()


def chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def safe_label(value: str | None) -> str:
    label = re.sub(r"[^A-Za-z0-9_]+", "", value or "LineageNode")
    if not label:
        return "LineageNode"
    if label[0].isdigit():
        label = f"Node{label}"
    return label


def safe_relationship_type(value: str | None) -> str:
    rel_type = re.sub(r"[^A-Za-z0-9_]+", "_", value or "IS_LINKED_TO").strip("_").upper()
    if not rel_type:
        return "IS_LINKED_TO"
    if rel_type[0].isdigit():
        rel_type = f"REL_{rel_type}"
    return rel_type


def neo4j_config(v2_config: dict[str, Any] | None = None) -> tuple[str, str, str]:
    v2_config = v2_config or {}
    uri = v2_config.get("neo4j_uri") or os.getenv("NEO4J_URI")
    user = v2_config.get("neo4j_user") or os.getenv("NEO4J_USER", "neo4j")
    password = v2_config.get("neo4j_password") or os.getenv("NEO4J_PASSWORD")
    if not uri or not password:
        raise SystemExit("NEO4J_URI and NEO4J_PASSWORD are required for graph build.")
    return uri, user, password


def collect_readiness(engine, export_id: str) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            "open_validation_errors": int(
                conn.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM migration_validation_finding
                        WHERE export_id = :export_id AND severity = 'ERROR' AND status = 'open'
                        """
                    ),
                    {"export_id": export_id},
                ).scalar_one()
            ),
            "eligible_objects": int(
                conn.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM migration_trusted_object_projection
                        WHERE export_id = :export_id
                        """
                    ),
                    {"export_id": export_id},
                ).scalar_one()
            ),
            "eligible_relationships": int(
                conn.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM migration_trusted_relationship_projection
                        WHERE export_id = :export_id
                        """
                    ),
                    {"export_id": export_id},
                ).scalar_one()
            ),
        }


def fetch_objects(engine, export_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT node_id, parent_node_id, object_type, name_label, name_tech,
                       path_full, path_hash, entity_type, data_type, status, app_code,
                       source_table, raw_payload
                FROM migration_trusted_object_projection
                WHERE export_id = :export_id
                ORDER BY object_type, node_id
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
    return [dict(row) for row in rows]


def fetch_relationships(engine, export_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, src_node_id, tgt_node_id, relationship_type, relationship_family,
                       source_table, link_type, status, raw_payload
                FROM migration_trusted_relationship_projection
                WHERE export_id = :export_id
                ORDER BY relationship_type, src_node_id, tgt_node_id
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
    return [dict(row) for row in rows]


def node_props(row: dict[str, Any], export_id: str) -> dict[str, Any]:
    props = {
        "node_id": row["node_id"],
        "parent_node_id": row.get("parent_node_id"),
        "object_type": row.get("object_type"),
        "name_label": row.get("name_label"),
        "name_tech": row.get("name_tech"),
        "path_full": row.get("path_full"),
        "path_hash": row.get("path_hash"),
        "entity_type": row.get("entity_type"),
        "data_type": row.get("data_type"),
        "status": row.get("status"),
        "app_code": row.get("app_code"),
        "source_table": row.get("source_table"),
        "hierarchy_depth": row.get("hierarchy_depth"),
        "root_source_id": row.get("root_source_id"),
        "hierarchy_cycle_detected": row.get("hierarchy_cycle_detected"),
        "hierarchy_missing_parent": row.get("hierarchy_missing_parent"),
        "duplicate_role_node": row.get("duplicate_role_node"),
        "export_id": export_id,
        "migration_version": "v2",
    }
    raw_payload = row.get("raw_payload") or {}
    if row.get("object_type") == "Usage" and isinstance(raw_payload, dict):
        props["usage_uuid"] = raw_payload.get("v_tech_ident_entt") or raw_payload.get("usage_uuid") or row["node_id"]
        props["usage_path"] = raw_payload.get("v_path") or raw_payload.get("usage_path") or row.get("path_full")
        props["usage_kind"] = raw_payload.get("v_type_entt") or raw_payload.get("usage_kind") or row.get("entity_type")
        props["dataset_ref"] = raw_payload.get("v_dataset") or raw_payload.get("dataset_ref")
    return props


def relationship_props(row: dict[str, Any], export_id: str) -> dict[str, Any]:
    props = {
        "staging_id": row["id"],
        "relationship_type": row.get("relationship_type"),
        "relationship_family": row.get("relationship_family"),
        "source_table": row.get("source_table"),
        "link_type": row.get("link_type"),
        "status": row.get("status"),
        "export_id": export_id,
        "migration_version": "v2",
    }
    if row.get("source_table") == "usage_resolver":
        confidence_by_link_type = {
            "usage_app_code": 0.95,
            "usage_dataset_ref_exact": 0.9,
            "usage_dataset_ref_path_contains": 0.65,
        }
        props["edge_source"] = "usage_resolver"
        props["edge_confidence"] = confidence_by_link_type.get(str(row.get("link_type")), 0.7)
        props["match_method"] = row.get("link_type")
        props["edge_evidence"] = json.dumps(row.get("raw_payload") or {}, default=str)
    return props


def enrich_hierarchy_metadata(objects: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = compute_hierarchy_metadata(objects)
    for row in objects:
        row.update(metadata.get(str(row["node_id"]), {}))
    return {
        "cycle_count": sum(1 for item in metadata.values() if item.get("hierarchy_cycle_detected")),
        "missing_parent_count": sum(1 for item in metadata.values() if item.get("hierarchy_missing_parent")),
        "duplicate_role_node_count": sum(1 for item in metadata.values() if item.get("duplicate_role_node")),
        "rooted_node_count": sum(1 for item in metadata.values() if item.get("root_source_id")),
    }


def run_cypher(session, query: str, rows: list[dict[str, Any]] | None = None) -> None:
    result = session.run(query, rows=rows or [])
    result.consume()


def clear_graph_in_batches(session, batch_size: int) -> int:
    total_deleted = 0
    while True:
        record = session.run(
            """
            MATCH (n)
            WITH n LIMIT $batch_size
            DETACH DELETE n
            RETURN count(*) AS deleted
            """,
            batch_size=batch_size,
        ).single()
        deleted = int(record["deleted"] if record else 0)
        total_deleted += deleted
        if deleted == 0:
            return total_deleted


def ensure_schema(session) -> None:
    queries = [
        """
        CREATE CONSTRAINT migration_v2_dg_object_node_id IF NOT EXISTS
        FOR (n:DataGalaxyObject)
        REQUIRE n.node_id IS UNIQUE
        """,
        """
        CREATE INDEX migration_v2_dg_object_export_id IF NOT EXISTS
        FOR (n:DataGalaxyObject)
        ON (n.export_id)
        """,
        """
        CREATE INDEX migration_v2_dg_object_status IF NOT EXISTS
        FOR (n:DataGalaxyObject)
        ON (n.status)
        """,
    ]
    for query in queries:
        run_cypher(session, query)


def load_nodes(session, objects: list[dict[str, Any]], export_id: str, batch_size: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    nodes_by_id: dict[str, dict[str, Any]] = {}
    for row in objects:
        label = safe_label(row.get("object_type"))
        counts[label] = counts.get(label, 0) + 1
        node_id = row["node_id"]
        props = node_props(row, export_id)
        existing = nodes_by_id.setdefault(
            node_id,
            {
                "node_id": node_id,
                "props": props,
                "labels": set(),
                "object_types": set(),
            },
        )
        existing["labels"].add(label)
        existing["object_types"].add(row.get("object_type") or label)
        for key, value in props.items():
            if existing["props"].get(key) in (None, "") and value not in (None, ""):
                existing["props"][key] = value

    base_rows = []
    label_rows: dict[str, list[dict[str, Any]]] = {}
    for node in nodes_by_id.values():
        node["props"]["object_types"] = sorted(node["object_types"])
        base_rows.append({"node_id": node["node_id"], "props": node["props"]})
        for label in node["labels"]:
            label_rows.setdefault(label, []).append({"node_id": node["node_id"]})

    base_query = """
    UNWIND $rows AS row
    MERGE (n:DataGalaxyObject {node_id: row.node_id})
    SET n += row.props
    """
    for batch in chunks(base_rows, batch_size):
        run_cypher(session, base_query, batch)

    for label, rows in sorted(label_rows.items()):
        query = f"""
        UNWIND $rows AS row
        MATCH (n:DataGalaxyObject {{node_id: row.node_id}})
        SET n:{label}
        """
        for batch in chunks(rows, batch_size):
            run_cypher(session, query, batch)
    counts["unique_node_id"] = len(nodes_by_id)
    return counts


def load_hierarchy(session, objects: list[dict[str, Any]], export_id: str, batch_size: int) -> int:
    type_by_node: dict[str, str | None] = {}
    for row in objects:
        node_id = str(row["node_id"])
        existing_types = set(object_types_for_node(row))
        if node_id in type_by_node and type_by_node[node_id]:
            existing_types.add(str(type_by_node[node_id]))
        type_by_node[node_id] = primary_object_type(sorted(existing_types))

    rows = [
        build_hierarchy_relationship_row(row, type_by_node, export_id)
        for row in objects
        if row.get("parent_node_id")
    ]
    total = 0
    for rel_type in ["CONTAINS", "HAS_FIELD"]:
        rel_rows = [row for row in rows if row["rel_type"] == rel_type]
        query = f"""
        UNWIND $rows AS row
        MATCH (parent:DataGalaxyObject {{node_id: row.parent_node_id}})
        MATCH (child:DataGalaxyObject {{node_id: row.node_id}})
        MERGE (parent)-[r:{rel_type}]->(child)
        SET r += row.props
        """
        for batch in chunks(rel_rows, batch_size):
            run_cypher(session, query, batch)
        total += len(rel_rows)
    return total


def build_hierarchy_relationship_row(
    row: dict[str, Any],
    type_by_node: dict[str, str | None],
    export_id: str,
) -> dict[str, Any]:
    node_id = str(row["node_id"])
    parent_node_id = str(row["parent_node_id"])
    child_type = type_by_node.get(node_id) or primary_object_type(object_types_for_node(row))
    parent_type = type_by_node.get(parent_node_id)
    classification = classify_hierarchy_edge(parent_type, child_type)
    evidence = {
        **classification.evidence,
        "child_node_id": node_id,
        "parent_node_id": parent_node_id,
        "child_path_full": row.get("path_full"),
    }
    return {
        "node_id": node_id,
        "parent_node_id": parent_node_id,
        "rel_type": classification.relationship_type,
        "props": {
            "export_id": export_id,
            "migration_version": "v2",
            "source_column": "parent_node_id",
            "edge_source": "direct_parent_id",
            "edge_confidence": classification.confidence,
            "hierarchy_classification": classification.classification,
            "edge_evidence": json.dumps(evidence, default=str),
        },
    }


def load_relationships(session, relationships: list[dict[str, Any]], export_id: str, batch_size: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in relationships:
        rel_type = safe_relationship_type(row.get("relationship_type"))
        grouped.setdefault(rel_type, []).append(
            {
                "src_node_id": row["src_node_id"],
                "tgt_node_id": row["tgt_node_id"],
                "props": relationship_props(row, export_id),
            }
        )

    for rel_type, rows in sorted(grouped.items()):
        query = f"""
        UNWIND $rows AS row
        MATCH (src:DataGalaxyObject {{node_id: row.src_node_id}})
        MATCH (tgt:DataGalaxyObject {{node_id: row.tgt_node_id}})
        MERGE (src)-[r:{rel_type}]->(tgt)
        SET r += row.props
        """
        for batch in chunks(rows, batch_size):
            run_cypher(session, query, batch)
        counts[rel_type] = len(rows)
    return counts


def main() -> None:
    args = parse_args()
    v2_config = config_section(load_env_config(args.env_config), "v2") if args.env_config else {}
    engine = postgres_engine_from_url(v2_config["postgres_url"]) if v2_config.get("postgres_url") else postgres_engine()
    ensure_tables(engine, ["catalog_object_staging", "catalog_relationship_staging", "migration_validation_finding"])
    readiness = collect_readiness(engine, args.export_id)
    if readiness["open_validation_errors"] and not args.force:
        status = "blocked_by_validation"
        payload = {"export_id": args.export_id, "status": status, **readiness}
        write_json_report(args.export_id, "graph_build_report.json", payload)
        write_markdown_report(
            args.export_id,
            "graph_build_report.md",
            "Migration V2 Graph Build Report",
            [("Status", "`blocked_by_validation`. Run validation report review or pass `--force`.")],
        )
        raise SystemExit("Graph build blocked by open validation errors. Use --force only for sandbox experiments.")

    if args.dry_run:
        payload = {"export_id": args.export_id, "status": "dry_run_ready", **readiness}
        write_json_report(args.export_id, "graph_build_report.json", payload)
        write_markdown_report(
            args.export_id,
            "graph_build_report.md",
            "Migration V2 Graph Build Report",
            [("Status", "`dry_run_ready`"), ("Counts", "\n".join(f"- `{k}`: {v}" for k, v in readiness.items()))],
        )
        return

    uri, user, password = neo4j_config(v2_config)
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise SystemExit("The neo4j Python package is required. Install backend requirements in your environment.") from exc

    usage_resolution = {"status": "skipped"}
    if not args.skip_usage_resolver:
        usage_resolution = resolve_usage_relationships(
            engine,
            args.export_id,
            dataset_path_match_limit=args.usage_dataset_path_limit,
        )
        LOGGER.info("Resolved %s usage relationships", usage_resolution.get("total_relationships"))

    objects = fetch_objects(engine, args.export_id)
    relationships = fetch_relationships(engine, args.export_id)
    hierarchy_metadata_counts = enrich_hierarchy_metadata(objects)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            if args.clear_first:
                LOGGER.info("Clearing Neo4j graph in batches of %s nodes", args.clear_batch_size)
                deleted = clear_graph_in_batches(session, args.clear_batch_size)
                LOGGER.info("Cleared %s Neo4j nodes", deleted)
            ensure_schema(session)
            node_counts = load_nodes(session, objects, args.export_id, args.batch_size)
            hierarchy_count = load_hierarchy(session, objects, args.export_id, args.batch_size)
            relationship_counts = load_relationships(session, relationships, args.export_id, args.batch_size)
    finally:
        driver.close()

    payload = {
        "export_id": args.export_id,
        "status": "completed",
        "neo4j_uri": uri,
        "clear_first": args.clear_first,
        "node_counts": node_counts,
        "hierarchy_metadata_counts": hierarchy_metadata_counts,
        "hierarchy_relationship_count": hierarchy_count,
        "usage_resolution": usage_resolution,
        "lineage_relationship_counts": relationship_counts,
    }
    json_path = write_json_report(args.export_id, "graph_build_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "graph_build_report.md",
        "Migration V2 Graph Build Report",
        [
            ("Status", "`completed`"),
            ("Neo4j URI", f"`{uri}`"),
            (
                "Hierarchy Metadata",
                "\n".join(f"- `{key}`: {value}" for key, value in sorted(hierarchy_metadata_counts.items())) or "None.",
            ),
            ("Nodes", "\n".join(f"- `{key}`: {value}" for key, value in sorted(node_counts.items())) or "None."),
            (
                "Lineage Relationships",
                "\n".join(f"- `{key}`: {value}" for key, value in sorted(relationship_counts.items())) or "None.",
            ),
            (
                "Usage Resolver",
                "\n".join(
                    f"- `{key}`: {value}"
                    for key, value in sorted((usage_resolution.get("relationship_counts") or {}).items())
                )
                or f"`{usage_resolution.get('status')}`",
            ),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
