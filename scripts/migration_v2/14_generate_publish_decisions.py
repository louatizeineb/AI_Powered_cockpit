from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import text

from _common import (
    REPORT_ROOT,
    ROOT,
    config_section,
    ensure_tables,
    json_param,
    load_contract,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)


LOGGER = setup_logging("migration_v2.publish_decisions")

DECISION_SQL = ROOT / "backend" / "migrations" / "sql" / "011_migration_v2_publish_decision_layer.sql"

ROLE_TO_CANONICAL = {
    "Source": "Source",
    "Relational": "Source",
    "NonRelational": "Source",
    "NoSql": "Source",
    "Container": "Container",
    "Directory": "Container",
    "Model": "Container",
    "Structure": "Structure",
    "SubStructure": "Structure",
    "View": "Structure",
    "File": "Structure",
    "Document": "Structure",
    "OpenDataSet": "Structure",
    "Field": "Field",
    "Usage": "Usage",
    "UsageField": "Usage",
    "Application": "Usage",
    "Process": "Usage",
    "Screen": "Usage",
    "DataSet": "Usage",
    "Report": "Usage",
    "Algorithm": "Usage",
    "Dashboard": "Usage",
    "Feature": "Usage",
    "UsageComponent": "Usage",
    "BusinessTerm": "BusinessTerm",
    "DataProcessing": "DataProcessing",
    "DataProcessingItem": "DataProcessingItem",
    "Concept": "Concept",
    "DataFlow": "DataFlow",
    "DataProduct": "DataProduct",
    "Dimension": "Dimension",
    "DimensionGroup": "DimensionGroup",
    "Indicator": "Indicator",
    "Universe": "Universe",
    "UseCase": "UseCase",
}

INVERSE_RELATIONSHIPS = {
    "CALLS": "IS_CALLED_BY",
    "IS_CALLED_BY": "CALLS",
    "IMPLEMENTS": "IS_IMPLEMENTED_BY",
    "IS_IMPLEMENTED_BY": "IMPLEMENTS",
    "GENERALIZES": "SPECIALIZES",
    "SPECIALIZES": "GENERALIZES",
    "HAS_FOR_SOURCE": "IS_SOURCE_OF",
    "IS_SOURCE_OF": "HAS_FOR_SOURCE",
    "HAS_FOR_UNIVERSE": "IS_UNIVERSE_OF",
    "IS_UNIVERSE_OF": "HAS_FOR_UNIVERSE",
    "HAS_FOR_RECORDING_SYSTEM": "IS_RECORDING_SYSTEM_FOR",
    "IS_RECORDING_SYSTEM_FOR": "HAS_FOR_RECORDING_SYSTEM",
    "USES": "IS_USED_BY",
    "IS_USED_BY": "USES",
    "IS_USAGE_SOURCE_FOR": "IS_USAGE_DESTINATION_FOR",
    "IS_USAGE_DESTINATION_FOR": "IS_USAGE_SOURCE_FOR",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate migration_v2 publish decision-layer tables and reports."
    )
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    parser.add_argument(
        "--accept-rootless-orphans",
        action="store_true",
        help="Explicitly accept the current rootless Field/UsageField orphan set.",
    )
    parser.add_argument(
        "--orphan-rationale",
        default="",
        help="Human rationale required when --accept-rootless-orphans is used.",
    )
    return parser.parse_args()


def engine_from_args(args: argparse.Namespace):
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
        if v2_config.get("postgres_url"):
            return postgres_engine_from_url(v2_config["postgres_url"])
    return postgres_engine()


def v2_config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if not args.env_config:
        return {}
    return config_section(load_env_config(args.env_config), "v2")


def load_report(export_id: str, filename: str) -> dict[str, Any] | None:
    path = REPORT_ROOT / export_id / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def apply_decision_schema(engine) -> None:
    sql = DECISION_SQL.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)


def replace_decision_rows(engine, table_name: str, export_id: str, rows: list[dict[str, Any]]) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {table_name} WHERE export_id = :export_id"), {"export_id": export_id})
        if not rows:
            return
        columns = list(rows[0])
        values_sql = ", ".join(f":{column}" for column in columns)
        conn.execute(
            text(f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({values_sql})"),
            rows,
        )


def pg_array(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def canonical_role_for(roles: list[str]) -> str | None:
    canonical_roles = sorted({ROLE_TO_CANONICAL.get(role, role) for role in roles if role})
    if not canonical_roles:
        return None
    if len(canonical_roles) == 1:
        return canonical_roles[0]
    priority = ["Source", "Container", "Structure", "Field", "Usage", "BusinessTerm"]
    for candidate in priority:
        if candidate in canonical_roles:
            return candidate
    return canonical_roles[0]


def classify_role_resolution(row: dict[str, Any]) -> dict[str, Any]:
    roles = [str(value) for value in pg_array(row.get("object_types")) if value]
    parent_node_ids = [str(value) for value in pg_array(row.get("parent_node_ids")) if value]
    paths = [str(value) for value in pg_array(row.get("paths")) if value]
    labels = [str(value) for value in pg_array(row.get("labels")) if value]
    technical_names = [str(value) for value in pg_array(row.get("technical_names")) if value]
    source_tables = [str(value) for value in pg_array(row.get("source_tables")) if value]
    canonical_roles = sorted({ROLE_TO_CANONICAL.get(role, role) for role in roles})
    conflict_fields = [
        field
        for field, values in [
            ("parent_node_id", parent_node_ids),
            ("path_full", paths),
            ("name_label", labels),
            ("name_tech", technical_names),
        ]
        if len(values) > 1
    ]

    canonical_role = canonical_role_for(roles)
    status = "accepted"
    reason = (
        "Multi-role DataGalaxy object resolved to the broader catalog role; all observed roles "
        "remain retained as metadata."
    )
    if len(canonical_roles) > 1:
        status = "review"
        reason = "Observed roles span multiple canonical catalog families and need human role policy."
    if "parent_node_id" in conflict_fields:
        status = "blocked"
        reason = "Same node_id has conflicting parent_node_id values, so identity placement is ambiguous."
    elif "path_full" in conflict_fields:
        status = "review"
        reason = "Same node_id has conflicting path_full values; likely alias or moved path, but publish needs acceptance."
    elif set(conflict_fields).issubset({"name_label", "name_tech"}):
        status = "accepted"
        reason = (
            "Only label/technical-name metadata differs; parent and path identity are stable, "
            "so the alternates are retained as aliases."
        )

    evidence = {
        "row_count": int(row.get("row_count") or 0),
        "canonical_roles": canonical_roles,
        "parent_node_ids": parent_node_ids,
        "paths": paths,
        "labels": labels,
        "technical_names": technical_names,
        "source_tables": source_tables,
        "policy": "Prefer the broad catalog-level role while retaining specific DataGalaxy roles as metadata.",
    }
    return {
        "export_id": row["export_id"],
        "node_id": row["node_id"],
        "observed_roles": json_param(roles),
        "canonical_role": canonical_role,
        "retained_roles": json_param(roles),
        "conflict_fields": json_param(conflict_fields),
        "decision_status": status,
        "decision_reason": reason,
        "evidence": json_param(evidence),
    }


def generate_role_decisions(engine, export_id: str) -> dict[str, Any]:
    ensure_tables(engine, ["catalog_object_staging"])
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                WITH grouped AS (
                    SELECT
                        export_id,
                        node_id,
                        count(*) AS row_count,
                        array_agg(DISTINCT object_type ORDER BY object_type) AS object_types,
                        array_remove(array_agg(DISTINCT parent_node_id ORDER BY parent_node_id), NULL) AS parent_node_ids,
                        array_remove(array_agg(DISTINCT path_full ORDER BY path_full), NULL) AS paths,
                        array_remove(array_agg(DISTINCT name_label ORDER BY name_label), NULL) AS labels,
                        array_remove(array_agg(DISTINCT name_tech ORDER BY name_tech), NULL) AS technical_names,
                        array_agg(DISTINCT source_table ORDER BY source_table) AS source_tables
                    FROM catalog_object_staging
                    WHERE export_id = :export_id
                    GROUP BY export_id, node_id
                    HAVING count(*) > 1
                )
                SELECT *
                FROM grouped
                ORDER BY row_count DESC, node_id
                """
            ),
            {"export_id": export_id},
        ).mappings().all()

    decisions = [classify_role_resolution(dict(row)) for row in rows]
    replace_decision_rows(engine, "migration_role_resolution", export_id, decisions)

    status_counts = Counter(row["decision_status"] for row in decisions)
    role_pair_counts = Counter(" + ".join(json.loads(row["observed_roles"])) for row in decisions)
    conflict_counts = Counter(
        field
        for row in decisions
        for field in json.loads(row["conflict_fields"])
    )
    return {
        "export_id": export_id,
        "status": "ready" if not status_counts.get("blocked") and not status_counts.get("review") else "blocked",
        "total_duplicate_node_ids": len(decisions),
        "status_counts": dict(sorted(status_counts.items())),
        "role_pair_counts": dict(role_pair_counts.most_common(20)),
        "conflict_field_counts": dict(sorted(conflict_counts.items())),
        "blockers": role_blockers(status_counts),
        "samples": [row_sample(row) for row in decisions if row["decision_status"] != "accepted"][:50],
    }


def role_blockers(status_counts: Counter[str]) -> list[str]:
    blockers: list[str] = []
    if status_counts.get("blocked"):
        blockers.append(f"{status_counts['blocked']} duplicate node_ids have blocking role/identity conflicts.")
    if status_counts.get("review"):
        blockers.append(f"{status_counts['review']} duplicate node_ids require path or cross-family role acceptance.")
    return blockers


def row_sample(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": row.get("node_id"),
        "decision_status": row.get("decision_status"),
        "decision_reason": row.get("decision_reason"),
        "observed_roles": json.loads(row.get("observed_roles") or "[]"),
        "canonical_role": row.get("canonical_role"),
        "conflict_fields": json.loads(row.get("conflict_fields") or "[]"),
        "evidence": json.loads(row.get("evidence") or "{}"),
    }


def neo4j_driver_from_config(v2_config: dict[str, Any]):
    uri = v2_config.get("neo4j_uri") or os.getenv("NEO4J_URI")
    user = v2_config.get("neo4j_user") or os.getenv("NEO4J_USER")
    password = v2_config.get("neo4j_password") or os.getenv("NEO4J_PASSWORD")
    if not uri or not user or not password:
        return None, "neo4j_config_missing"
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return None, "neo4j_package_missing"
    try:
        return GraphDatabase.driver(uri, auth=(user, password)), None
    except BaseException as exc:  # noqa: BLE001
        return None, f"neo4j_driver_failed: {exc}"


def path_depth(path_full: str | None) -> int:
    if not path_full:
        return 0
    parts = [
        part
        for part in path_full.replace("/", "\\").split("\\")
        if part.strip() and part.strip().lower() != "null"
    ]
    return len(parts)


def is_placeholder_path(path_full: str | None) -> bool:
    if not path_full:
        return False
    parts = [part.strip().lower() for part in path_full.replace("/", "\\").split("\\") if part.strip()]
    return bool(parts) and all(part == "null" for part in parts)


def classify_orphan(row: dict[str, Any], accept_rootless: bool, rationale: str) -> dict[str, Any]:
    child_count = int(row.get("child_count") or 0)
    incoming_context_count = int(row.get("incoming_context_count") or 0)
    outgoing_context_count = int(row.get("outgoing_context_count") or 0)
    relationship_count = incoming_context_count + outgoing_context_count
    path_full = row.get("path_full")
    depth = path_depth(path_full)

    if child_count:
        orphan_class = "root_without_parent_but_parent_of_children"
        reason = "The node is rootless but has hierarchy children; it cannot be treated as a harmless leaf orphan."
    elif is_placeholder_path(path_full):
        orphan_class = "placeholder_path_missing_parent_metadata"
        reason = "The node has only null path placeholders and no parent_node_id; source metadata is incomplete."
    elif depth >= 2:
        orphan_class = "pathful_leaf_missing_parent_metadata"
        reason = "The path has parent segments but parent_node_id is missing; this looks like missed parent resolution."
    elif relationship_count:
        orphan_class = "lineage_endpoint_missing_parent_metadata"
        reason = "The node participates in non-hierarchy relationships but has no hierarchy parent."
    else:
        orphan_class = "isolated_rootless_leaf"
        reason = "The node has no hierarchy parent and no detected children; acceptance still needs explicit evidence."

    status = "accepted" if accept_rootless and rationale.strip() else "blocked"
    if status == "accepted":
        reason = f"Accepted by human policy: {rationale.strip()}"

    evidence = {
        "labels": row.get("labels") or [],
        "name_label": row.get("name_label"),
        "name_tech": row.get("name_tech"),
        "path_full": path_full,
        "path_depth": depth,
        "child_samples": row.get("child_samples") or [],
        "incoming_context_types": row.get("incoming_context_types") or [],
        "outgoing_context_types": row.get("outgoing_context_types") or [],
    }
    return {
        "export_id": row["export_id"],
        "node_id": row["node_id"],
        "object_type": row.get("object_type"),
        "orphan_class": orphan_class,
        "decision_status": status,
        "decision_reason": reason,
        "child_count": child_count,
        "relationship_count": relationship_count,
        "evidence": json_param(evidence),
    }


def fetch_neo4j_rootless_orphans(v2_config: dict[str, Any], export_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    driver, error = neo4j_driver_from_config(v2_config)
    if error:
        return [], [error]
    assert driver is not None
    try:
        with driver.session() as session:
            rows = session.run(
                """
                MATCH (n:DataGalaxyObject)
                WHERE n.export_id = $export_id
                  AND NOT (n)<-[:CONTAINS|HAS_FIELD]-()
                  AND n.parent_node_id IS NULL
                  AND (n:Field OR n:UsageField)
                CALL {
                    WITH n
                    OPTIONAL MATCH (n)-[:CONTAINS|HAS_FIELD]->(child:DataGalaxyObject)
                    RETURN count(DISTINCT child) AS child_count,
                           collect(DISTINCT child.node_id)[0..5] AS child_samples
                }
                CALL {
                    WITH n
                    OPTIONAL MATCH (n)-[out]->()
                    RETURN count(DISTINCT CASE WHEN NOT type(out) IN ['CONTAINS', 'HAS_FIELD'] THEN out END) AS outgoing_context_count,
                           [rel_type IN collect(DISTINCT type(out))
                            WHERE rel_type IS NOT NULL AND NOT rel_type IN ['CONTAINS', 'HAS_FIELD']][0..10] AS outgoing_context_types
                }
                CALL {
                    WITH n
                    OPTIONAL MATCH ()-[inc]->(n)
                    RETURN count(DISTINCT CASE WHEN NOT type(inc) IN ['CONTAINS', 'HAS_FIELD'] THEN inc END) AS incoming_context_count,
                           [rel_type IN collect(DISTINCT type(inc))
                            WHERE rel_type IS NOT NULL AND NOT rel_type IN ['CONTAINS', 'HAS_FIELD']][0..10] AS incoming_context_types
                }
                RETURN
                    n.node_id AS node_id,
                    coalesce(n.object_type, head([label IN labels(n) WHERE label <> 'DataGalaxyObject'])) AS object_type,
                    labels(n) AS labels,
                    n.name_label AS name_label,
                    n.name_tech AS name_tech,
                    n.path_full AS path_full,
                    child_count,
                    child_samples,
                    outgoing_context_count,
                    outgoing_context_types,
                    incoming_context_count,
                    incoming_context_types
                ORDER BY object_type, node_id
                """
                ,
                export_id=export_id,
            )
            return [{**dict(row), "export_id": export_id} for row in rows], []
    except BaseException as exc:  # noqa: BLE001
        return [], [f"neo4j_orphan_query_failed: {exc}"]
    finally:
        driver.close()


def generate_orphan_decisions(
    engine,
    export_id: str,
    v2_config: dict[str, Any],
    accept_rootless: bool,
    rationale: str,
) -> dict[str, Any]:
    rows, errors = fetch_neo4j_rootless_orphans(v2_config, export_id)
    decisions = [classify_orphan(row, accept_rootless, rationale) for row in rows]
    replace_decision_rows(engine, "migration_orphan_classification", export_id, decisions)

    graph_audit = load_report(export_id, "graph_audit_report.json") or {}
    expected_count = int(
        (((graph_audit.get("neo4j_graph") or {}).get("orphan_classification_counts") or {}).get(
            "root_without_parent_metadata"
        )
        or 0)
    )
    status_counts = Counter(row["decision_status"] for row in decisions)
    orphan_class_counts = Counter(row["orphan_class"] for row in decisions)
    parent_like_count = sum(1 for row in decisions if int(row["child_count"] or 0) > 0)
    lineage_endpoint_count = sum(1 for row in decisions if int(row["relationship_count"] or 0) > 0)

    blockers: list[str] = []
    if errors:
        blockers.extend(errors)
    if expected_count and expected_count != len(decisions):
        blockers.append(
            f"Expected {expected_count} root_without_parent_metadata orphans from graph audit, found {len(decisions)} exact Neo4j rows."
        )
    if status_counts.get("blocked"):
        blockers.append(f"{status_counts['blocked']} rootless Field/UsageField orphans remain unaccepted.")

    return {
        "export_id": export_id,
        "status": "ready" if not blockers else "blocked",
        "expected_rootless_orphan_count": expected_count,
        "classified_rootless_orphan_count": len(decisions),
        "status_counts": dict(sorted(status_counts.items())),
        "orphan_class_counts": dict(sorted(orphan_class_counts.items())),
        "parent_like_orphan_count": parent_like_count,
        "lineage_endpoint_orphan_count": lineage_endpoint_count,
        "accepted_rootless_orphans": bool(accept_rootless and rationale.strip() and not status_counts.get("blocked")),
        "errors": errors,
        "blockers": blockers,
        "samples": [row_sample_orphan(row) for row in decisions[:50]],
    }


def row_sample_orphan(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": row["node_id"],
        "object_type": row.get("object_type"),
        "orphan_class": row["orphan_class"],
        "decision_status": row["decision_status"],
        "child_count": row["child_count"],
        "relationship_count": row["relationship_count"],
        "evidence": json.loads(row.get("evidence") or "{}"),
    }


def contract_relationships() -> tuple[dict[str, list[str]], dict[str, str]]:
    mappings = load_contract().get("relationship_mappings") or {}
    raw_by_canonical: dict[str, list[str]] = {}
    family_by_canonical: dict[str, str] = {}
    for raw_value, spec in mappings.items():
        canonical = spec.get("canonical_type")
        if not canonical:
            continue
        raw_by_canonical.setdefault(str(canonical), []).append(str(raw_value))
        family_by_canonical[str(canonical)] = str(spec.get("family") or "unknown")
    return raw_by_canonical, family_by_canonical


def relationship_source_evidence(engine, export_id: str) -> dict[str, dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT relationship_type,
                       count(*) AS row_count,
                       array_remove(array_agg(DISTINCT link_type ORDER BY link_type), NULL) AS link_types,
                       array_agg(DISTINCT source_table ORDER BY source_table) AS source_tables
                FROM catalog_relationship_staging
                WHERE export_id = :export_id
                GROUP BY relationship_type
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
    return {
        str(row["relationship_type"]): {
            "row_count": int(row["row_count"]),
            "link_types": [str(value) for value in pg_array(row.get("link_types")) if value],
            "source_tables": [str(value) for value in pg_array(row.get("source_tables")) if value],
        }
        for row in rows
    }


def relationship_decision(
    row: dict[str, Any],
    raw_by_canonical: dict[str, list[str]],
    family_by_canonical: dict[str, str],
    source_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rel_type = str(row["metric_name"])
    parity_status = str(row.get("status"))
    inverse_type = INVERSE_RELATIONSHIPS.get(rel_type)
    raw_link_types = raw_by_canonical.get(rel_type, [])
    family = family_by_canonical.get(rel_type)
    evidence = {
        "family": family,
        "contract_raw_values": raw_link_types,
        "staging_source_evidence": source_evidence.get(rel_type, {}),
        "inverse_relationship_type": inverse_type,
    }

    decision_status = "accepted"
    explanation_class = "matched"
    reason = "v0 and v2 relationship counts match."
    required_action = "none"

    if parity_status == "v2_extra":
        if raw_link_types:
            explanation_class = "contract_typed_relationship"
            reason = (
                "v2 exposes this raw DataGalaxy link as a typed relationship from lien_link_entt; "
                "the baseline aggregate report did not break this type out."
            )
        else:
            explanation_class = "v2_enrichment_or_unmapped_type"
            decision_status = "review"
            reason = "v2 has an extra relationship type not mapped in the contract."
            required_action = "map or explicitly exclude this relationship type"
    elif rel_type == "Relationships" and parity_status == "different":
        decision_status = "review"
        explanation_class = "aggregate_not_directly_comparable"
        reason = (
            "The old baseline has an aggregate relationship total; v2 has typed relationships, "
            "so the total delta cannot be accepted without edge/type breakdown."
        )
        required_action = "export or accept baseline edge-level relationship type breakdown"
    elif rel_type == "HAS_FIELD" and parity_status == "different":
        decision_status = "blocked"
        explanation_class = "missing_hierarchy_edge"
        reason = "v2 is missing exactly one HAS_FIELD edge relative to the baseline."
        required_action = "identify and repair or explicitly accept the missing HAS_FIELD edge"
    elif rel_type == "IMPLEMENTS" and parity_status == "different":
        decision_status = "blocked"
        explanation_class = "missing_semantic_edges"
        reason = "v2 is missing 155 IMPLEMENTS links relative to the baseline."
        required_action = "classify each missing semantic link as repaired, excluded by policy, or baseline-only"
    elif parity_status == "missing_in_v2":
        decision_status = "blocked"
        explanation_class = "baseline_type_missing_in_v2"
        reason = "A relationship type present in the baseline is missing from v2."
        required_action = "repair migration mapping or explicitly exclude this relationship type"
    elif parity_status != "matched":
        decision_status = "review"
        explanation_class = "unclassified_delta"
        reason = "Relationship delta needs human review."
        required_action = "review"

    return {
        "export_id": row["export_id"],
        "relationship_type": rel_type,
        "baseline_value": row.get("baseline_value"),
        "v2_value": row.get("v2_value"),
        "delta_value": row.get("delta_value"),
        "parity_status": parity_status,
        "decision_status": decision_status,
        "explanation_class": explanation_class,
        "inverse_relationship_type": inverse_type,
        "raw_link_types": json_param(raw_link_types),
        "decision_reason": reason,
        "required_action": required_action,
        "evidence": json_param(evidence),
    }


def generate_relationship_decisions(engine, export_id: str) -> dict[str, Any]:
    parity = load_report(export_id, "relationship_parity_report.json")
    if not parity:
        replace_decision_rows(engine, "migration_relationship_explanation", export_id, [])
        return {
            "export_id": export_id,
            "status": "blocked",
            "blockers": ["relationship_parity_report.json is missing."],
        }

    raw_by_canonical, family_by_canonical = contract_relationships()
    source_evidence = relationship_source_evidence(engine, export_id)
    decisions = [
        relationship_decision(dict(row), raw_by_canonical, family_by_canonical, source_evidence)
        for row in parity.get("rows") or []
    ]
    replace_decision_rows(engine, "migration_relationship_explanation", export_id, decisions)

    status_counts = Counter(row["decision_status"] for row in decisions)
    explanation_counts = Counter(row["explanation_class"] for row in decisions)
    blockers: list[str] = []
    if status_counts.get("blocked"):
        blockers.append(f"{status_counts['blocked']} relationship parity rows remain blocked.")
    if status_counts.get("review"):
        blockers.append(f"{status_counts['review']} relationship parity rows need human acceptance.")

    return {
        "export_id": export_id,
        "status": "ready" if not blockers else "blocked",
        "status_counts": dict(sorted(status_counts.items())),
        "explanation_class_counts": dict(sorted(explanation_counts.items())),
        "bidirectional_pairs": {
            rel_type: inverse
            for rel_type, inverse in sorted(INVERSE_RELATIONSHIPS.items())
            if any(row["relationship_type"] == rel_type for row in decisions)
        },
        "blockers": blockers,
        "samples": [row_sample_relationship(row) for row in decisions if row["decision_status"] != "accepted"][:50],
    }


def row_sample_relationship(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "relationship_type": row["relationship_type"],
        "parity_status": row["parity_status"],
        "decision_status": row["decision_status"],
        "explanation_class": row["explanation_class"],
        "delta_value": row.get("delta_value"),
        "required_action": row.get("required_action"),
        "decision_reason": row["decision_reason"],
        "inverse_relationship_type": row.get("inverse_relationship_type"),
        "raw_link_types": json.loads(row.get("raw_link_types") or "[]"),
    }


def write_role_report(payload: dict[str, Any]) -> None:
    export_id = payload["export_id"]
    write_json_report(export_id, "role_resolution_report.json", payload)
    write_markdown_report(
        export_id,
        "role_resolution_report.md",
        "Migration V2 Role Resolution Report",
        [
            ("Status", f"`{payload['status']}`"),
            (
                "Policy",
                "Prefer the broad catalog-level role for decisioning, retain all DataGalaxy-specific roles as metadata.",
            ),
            (
                "Counts",
                "\n".join(
                    [
                        f"- `total_duplicate_node_ids`: {payload['total_duplicate_node_ids']}",
                        f"- `status_counts`: `{payload['status_counts']}`",
                        f"- `conflict_field_counts`: `{payload['conflict_field_counts']}`",
                    ]
                ),
            ),
            (
                "Top Role Pairs",
                "\n".join(f"- `{key}`: {value}" for key, value in payload["role_pair_counts"].items()) or "None.",
            ),
            ("Blockers", "\n".join(f"- {item}" for item in payload["blockers"]) or "None."),
        ],
    )


def write_orphan_report(payload: dict[str, Any]) -> None:
    export_id = payload["export_id"]
    write_json_report(export_id, "orphan_classification_decision_report.json", payload)
    write_markdown_report(
        export_id,
        "orphan_classification_decision_report.md",
        "Migration V2 Orphan Classification Decision Report",
        [
            ("Status", f"`{payload['status']}`"),
            (
                "Counts",
                "\n".join(
                    [
                        f"- `expected_rootless_orphan_count`: {payload['expected_rootless_orphan_count']}",
                        f"- `classified_rootless_orphan_count`: {payload['classified_rootless_orphan_count']}",
                        f"- `parent_like_orphan_count`: {payload['parent_like_orphan_count']}",
                        f"- `lineage_endpoint_orphan_count`: {payload['lineage_endpoint_orphan_count']}",
                        f"- `orphan_class_counts`: `{payload['orphan_class_counts']}`",
                    ]
                ),
            ),
            ("Blockers", "\n".join(f"- {item}" for item in payload["blockers"]) or "None."),
        ],
    )


def write_relationship_report(payload: dict[str, Any]) -> None:
    export_id = payload["export_id"]
    write_json_report(export_id, "relationship_explanation_decision_report.json", payload)
    write_markdown_report(
        export_id,
        "relationship_explanation_decision_report.md",
        "Migration V2 Relationship Explanation Decision Report",
        [
            ("Status", f"`{payload['status']}`"),
            (
                "Counts",
                "\n".join(
                    [
                        f"- `status_counts`: `{payload.get('status_counts', {})}`",
                        f"- `explanation_class_counts`: `{payload.get('explanation_class_counts', {})}`",
                    ]
                ),
            ),
            (
                "Bidirectional Policy",
                "\n".join(
                    f"- `{key}` <-> `{value}`"
                    for key, value in (payload.get("bidirectional_pairs") or {}).items()
                    if key < value
                )
                or "None detected.",
            ),
            ("Blockers", "\n".join(f"- {item}" for item in payload["blockers"]) or "None."),
        ],
    )


def write_consolidated_report(
    export_id: str,
    role_payload: dict[str, Any],
    orphan_payload: dict[str, Any],
    relationship_payload: dict[str, Any],
) -> dict[str, Any]:
    blockers = (
        [f"role_resolution: {item}" for item in role_payload.get("blockers", [])]
        + [f"orphan_classification: {item}" for item in orphan_payload.get("blockers", [])]
        + [f"relationship_explanation: {item}" for item in relationship_payload.get("blockers", [])]
    )
    status = "ready" if not blockers else "blocked"
    payload = {
        "export_id": export_id,
        "status": status,
        "reports": {
            "role_resolution_report.json": role_payload["status"],
            "orphan_classification_decision_report.json": orphan_payload["status"],
            "relationship_explanation_decision_report.json": relationship_payload["status"],
        },
        "decision_tables": [
            "migration_role_resolution",
            "migration_orphan_classification",
            "migration_relationship_explanation",
        ],
        "blockers": blockers,
        "publish_decision": "NO-GO" if blockers else "GO",
    }
    write_json_report(export_id, "publish_decision_layer_report.json", payload)
    write_markdown_report(
        export_id,
        "publish_decision_layer_report.md",
        "Migration V2 Publish Decision Layer Report",
        [
            ("Decision", f"`{payload['publish_decision']}`"),
            (
                "Component Status",
                "\n".join(f"- `{key}`: `{value}`" for key, value in payload["reports"].items()),
            ),
            (
                "Decision Tables",
                "\n".join(f"- `{table}`" for table in payload["decision_tables"]),
            ),
            ("Blockers", "\n".join(f"- {item}" for item in blockers) or "None."),
        ],
    )
    return payload


def update_readiness_packet(export_id: str, decision_payload: dict[str, Any]) -> None:
    packet_path = REPORT_ROOT / export_id / "publish_readiness_packet.md"
    existing = packet_path.read_text(encoding="utf-8") if packet_path.exists() else ""
    marker = "## Decision Layer"
    section = "\n".join(
        [
            marker,
            "",
            f"- `publish_decision_layer_report`: `{decision_payload['status']}`",
            f"- `publish_decision`: `{decision_payload['publish_decision']}`",
            "- Required decision tables:",
            "  - `migration_role_resolution`",
            "  - `migration_orphan_classification`",
            "  - `migration_relationship_explanation`",
            "",
            "### Decision Layer Blockers",
            "",
            "\n".join(f"- {item}" for item in decision_payload["blockers"]) or "None.",
            "",
        ]
    )
    if marker in existing:
        existing = existing.split(marker, 1)[0].rstrip() + "\n\n" + section
    else:
        existing = existing.rstrip() + "\n\n" + section
    packet_path.write_text(existing, encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.accept_rootless_orphans and not args.orphan_rationale.strip():
        raise SystemExit("--accept-rootless-orphans requires --orphan-rationale.")

    engine = engine_from_args(args)
    v2_config = v2_config_from_args(args)
    ensure_tables(engine, ["migration_export_run", "catalog_object_staging", "catalog_relationship_staging"])
    apply_decision_schema(engine)

    role_payload = generate_role_decisions(engine, args.export_id)
    orphan_payload = generate_orphan_decisions(
        engine,
        args.export_id,
        v2_config,
        args.accept_rootless_orphans,
        args.orphan_rationale,
    )
    relationship_payload = generate_relationship_decisions(engine, args.export_id)

    write_role_report(role_payload)
    write_orphan_report(orphan_payload)
    write_relationship_report(relationship_payload)
    decision_payload = write_consolidated_report(args.export_id, role_payload, orphan_payload, relationship_payload)
    update_readiness_packet(args.export_id, decision_payload)
    LOGGER.info("Publish decision layer generated with status %s", decision_payload["status"])


if __name__ == "__main__":
    main()
