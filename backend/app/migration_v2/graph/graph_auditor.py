from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


EXPECTED_HIERARCHY_PAIRS = {
    ("Source", "Container"),
    ("Source", "Structure"),
    ("Container", "Container"),
    ("Container", "Structure"),
    ("Structure", "Structure"),
    ("Structure", "Field"),
}

ALLOWED_IRREGULAR_HIERARCHY_PAIRS = {
    ("Source", "Field"),
    ("Container", "Field"),
    ("Field", "Field"),
}

DATAGALAXY_NATIVE_HIERARCHY_PAIRS = {
    ("Usage", "Usage"),
    ("File", "Field"),
    ("File", "Structure"),
    ("SubStructure", "Field"),
    ("SubStructure", "Structure"),
    ("Document", "Field"),
    ("Document", "Structure"),
    ("Directory", "Container"),
    ("Directory", "Structure"),
    ("Model", "Container"),
    ("Model", "Structure"),
    ("Application", "Usage"),
    ("Process", "Usage"),
    ("Screen", "Usage"),
    ("DataSet", "Usage"),
    ("Report", "Usage"),
    ("Algorithm", "Usage"),
    ("UsageComponent", "Usage"),
    ("View", "Field"),
    ("Feature", "Usage"),
}


@dataclass(frozen=True)
class HierarchyClassification:
    classification: str
    confidence: float
    relationship_type: str
    evidence: dict[str, Any]


def object_types_for_node(row: dict[str, Any]) -> list[str]:
    value = row.get("object_types")
    if isinstance(value, list):
        return sorted({str(item) for item in value if item})
    object_type = row.get("object_type")
    return [str(object_type)] if object_type else []


def primary_object_type(types: list[str]) -> str | None:
    priority = [
        "Source",
        "Container",
        "Structure",
        "Field",
        "Usage",
        "BusinessTerm",
        "DataProcessing",
        "DataProcessingItem",
    ]
    for candidate in priority:
        if candidate in types:
            return candidate
    return types[0] if types else None


def classify_hierarchy_edge(parent_type: str | None, child_type: str | None) -> HierarchyClassification:
    relationship_type = "HAS_FIELD" if child_type == "Field" else "CONTAINS"
    pair = (parent_type, child_type)
    if parent_type is None:
        classification = "missing_parent"
        confidence = 0.0
    elif pair in EXPECTED_HIERARCHY_PAIRS:
        classification = "type_expected"
        confidence = 1.0
    elif pair in DATAGALAXY_NATIVE_HIERARCHY_PAIRS:
        classification = "type_datagalaxy_native"
        confidence = 0.9
    elif pair in ALLOWED_IRREGULAR_HIERARCHY_PAIRS:
        classification = "type_irregular_but_allowed"
        confidence = 0.8
    else:
        classification = "type_irregular_but_allowed"
        confidence = 0.6
    return HierarchyClassification(
        classification=classification,
        confidence=confidence,
        relationship_type=relationship_type,
        evidence={
            "rule": "parent.v_tech_ident_entt = child.v_drct_prnt_entt_ident",
            "parent_type": parent_type,
            "child_type": child_type,
            "allowed_expected_pairs": sorted(f"{src}->{dst}" for src, dst in EXPECTED_HIERARCHY_PAIRS),
            "datagalaxy_native_pairs": sorted(f"{src}->{dst}" for src, dst in DATAGALAXY_NATIVE_HIERARCHY_PAIRS),
            "allowed_irregular_pairs": sorted(f"{src}->{dst}" for src, dst in ALLOWED_IRREGULAR_HIERARCHY_PAIRS),
        },
    )


def fetch_hierarchy_rows(engine: Engine, export_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    node_id,
                    parent_node_id,
                    array_agg(DISTINCT object_type ORDER BY object_type) AS object_types,
                    min(object_type) AS object_type,
                    min(path_full) AS path_full,
                    min(status) AS status
                FROM catalog_object_staging
                WHERE export_id = :export_id AND is_graph_eligible
                GROUP BY node_id, parent_node_id
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
    return [dict(row) for row in rows]


def compute_hierarchy_metadata(rows: list[dict[str, Any]], max_depth: int = 200) -> dict[str, dict[str, Any]]:
    by_node: dict[str, dict[str, Any]] = {}
    parent_by_node: dict[str, str | None] = {}
    type_by_node: dict[str, str | None] = {}
    roles_by_node: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        node_id = str(row["node_id"])
        types = object_types_for_node(row)
        roles_by_node[node_id].update(types)
        existing = by_node.setdefault(node_id, dict(row))
        existing_types = set(object_types_for_node(existing)) | set(types)
        existing["object_types"] = sorted(existing_types)
        if not existing.get("parent_node_id") and row.get("parent_node_id"):
            existing["parent_node_id"] = row.get("parent_node_id")

    for node_id, row in by_node.items():
        parent_by_node[node_id] = str(row["parent_node_id"]) if row.get("parent_node_id") else None
        type_by_node[node_id] = primary_object_type(object_types_for_node(row))

    metadata: dict[str, dict[str, Any]] = {}
    for node_id, row in by_node.items():
        seen: set[str] = set()
        cursor: str | None = node_id
        depth = 0
        cycle_detected = False
        missing_parent = False
        root_source_id: str | None = None
        chain: list[str] = []

        while cursor:
            if cursor in seen:
                cycle_detected = True
                break
            seen.add(cursor)
            chain.append(cursor)
            parent_id = parent_by_node.get(cursor)
            if not parent_id:
                root_source_id = cursor if type_by_node.get(cursor) == "Source" else None
                break
            if parent_id not in parent_by_node:
                missing_parent = True
                break
            cursor = parent_id
            depth += 1
            if depth > max_depth:
                cycle_detected = True
                break

        if root_source_id is None:
            for candidate in reversed(chain):
                if type_by_node.get(candidate) == "Source":
                    root_source_id = candidate
                    break

        metadata[node_id] = {
            "hierarchy_depth": depth,
            "root_source_id": root_source_id,
            "hierarchy_cycle_detected": cycle_detected,
            "hierarchy_missing_parent": missing_parent,
            "duplicate_role_node": len(roles_by_node[node_id]) > 1,
            "object_types": object_types_for_node(row),
        }
    return metadata


def audit_staging_hierarchy(engine: Engine, export_id: str) -> dict[str, Any]:
    rows = fetch_hierarchy_rows(engine, export_id)
    metadata = compute_hierarchy_metadata(rows)
    by_node = {str(row["node_id"]): row for row in rows}
    type_by_node = {
        node_id: primary_object_type(object_types_for_node(row))
        for node_id, row in by_node.items()
    }

    classification_counts: Counter[str] = Counter()
    edge_pair_counts: Counter[str] = Counter()
    missing_parent_samples: list[dict[str, Any]] = []
    irregular_samples: list[dict[str, Any]] = []

    for node_id, row in by_node.items():
        parent_id = row.get("parent_node_id")
        if not parent_id:
            continue
        parent_id = str(parent_id)
        child_type = type_by_node.get(node_id)
        parent_type = type_by_node.get(parent_id)
        classification = classify_hierarchy_edge(parent_type, child_type)
        classification_counts[classification.classification] += 1
        edge_pair_counts[f"{parent_type or '<missing>'}->{child_type or '<unknown>'}"] += 1
        if classification.classification == "missing_parent" and len(missing_parent_samples) < 25:
            missing_parent_samples.append(
                {
                    "node_id": node_id,
                    "parent_node_id": parent_id,
                    "child_type": child_type,
                    "path_full": row.get("path_full"),
                }
            )
        if classification.classification == "type_irregular_but_allowed" and len(irregular_samples) < 25:
            irregular_samples.append(
                {
                    "node_id": node_id,
                    "parent_node_id": parent_id,
                    "parent_type": parent_type,
                    "child_type": child_type,
                    "path_full": row.get("path_full"),
                }
            )

    depth_counts = Counter(str(item["hierarchy_depth"]) for item in metadata.values())
    cycle_nodes = [node_id for node_id, item in metadata.items() if item["hierarchy_cycle_detected"]]
    duplicate_role_nodes = [node_id for node_id, item in metadata.items() if item["duplicate_role_node"]]

    with engine.connect() as conn:
        implements_count = int(
            conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM catalog_relationship_staging
                    WHERE export_id = :export_id AND relationship_type = 'IMPLEMENTS'
                    """
                ),
                {"export_id": export_id},
            ).scalar_one()
        )
        usage_object_count = int(
            conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM catalog_object_staging
                    WHERE export_id = :export_id AND object_type = 'Usage'
                    """
                ),
                {"export_id": export_id},
            ).scalar_one()
        )
        usage_relationship_count = int(
            conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM catalog_relationship_staging
                    WHERE export_id = :export_id
                      AND (
                          relationship_type ILIKE '%USAGE%'
                          OR link_type ILIKE '%usage%'
                      )
                    """
                ),
                {"export_id": export_id},
            ).scalar_one()
        )
        usage_resolver_relationship_count = int(
            conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM catalog_relationship_staging
                    WHERE export_id = :export_id
                      AND source_table = 'usage_resolver'
                    """
                ),
                {"export_id": export_id},
            ).scalar_one()
        )

    return {
        "export_id": export_id,
        "staging_node_count": len(by_node),
        "hierarchy_edge_count": sum(classification_counts.values()),
        "hierarchy_classification_counts": dict(sorted(classification_counts.items())),
        "hierarchy_edge_pair_counts": dict(edge_pair_counts.most_common()),
        "hierarchy_depth_distribution": dict(sorted(depth_counts.items(), key=lambda item: int(item[0]))),
        "missing_parent_count": classification_counts.get("missing_parent", 0),
        "missing_parent_samples": missing_parent_samples,
        "cycle_count": len(cycle_nodes),
        "cycle_samples": cycle_nodes[:25],
        "duplicate_role_node_count": len(duplicate_role_nodes),
        "duplicate_role_node_samples": duplicate_role_nodes[:25],
        "irregular_allowed_count": classification_counts.get("type_irregular_but_allowed", 0),
        "irregular_allowed_samples": irregular_samples,
        "implements_relationship_count": implements_count,
        "usage_object_count": usage_object_count,
        "usage_relationship_count": usage_relationship_count,
        "usage_resolver_relationship_count": usage_resolver_relationship_count,
    }


def audit_neo4j_graph(driver: Any) -> tuple[dict[str, Any], list[str]]:
    try:
        with driver.session() as session:
            node_total = session.run("MATCH (n) RETURN count(n) AS count").single()["count"]
            relationship_total = session.run("MATCH ()-[r]->() RETURN count(r) AS count").single()["count"]
            relationship_counts = {
                row["relationship_type"]: int(row["count"])
                for row in session.run(
                    """
                    MATCH ()-[r]->()
                    RETURN type(r) AS relationship_type, count(*) AS count
                    ORDER BY relationship_type
                    """
                )
            }
            orphan_count = int(
                session.run(
                    """
                    MATCH (n:DataGalaxyObject)
                    WHERE NOT (n)<-[:CONTAINS|HAS_FIELD]-()
                      AND NOT n:Source
                    RETURN count(n) AS count
                    """
                ).single()["count"]
            )
            orphan_class_rows = session.run(
                """
                MATCH (n:DataGalaxyObject)
                WHERE NOT (n)<-[:CONTAINS|HAS_FIELD]-()
                WITH n, labels(n) AS node_labels
                WITH n, node_labels,
                    CASE
                        WHEN 'Source' IN node_labels THEN 'source_root_expected'
                        WHEN 'Usage' IN node_labels AND n.parent_node_id IS NULL THEN 'usage_root_expected'
                        WHEN 'Usage' IN node_labels THEN 'usage_context_needs_review'
                        WHEN 'BusinessTerm' IN node_labels THEN 'semantic_term_expected_standalone'
                        WHEN 'DataProcessing' IN node_labels OR 'DataProcessingItem' IN node_labels THEN 'processing_context_expected_standalone'
                        WHEN any(label IN node_labels WHERE label IN [
                            'Application', 'Algorithm', 'Concept', 'DataFlow', 'DataProduct',
                            'Dimension', 'DimensionGroup', 'Feature', 'Indicator', 'Model',
                            'Process', 'Universe', 'UseCase'
                        ]) THEN 'non_catalog_context_expected_standalone'
                        WHEN n.parent_node_id IS NULL THEN 'root_without_parent_metadata'
                        ELSE 'catalog_orphan_needs_review'
                    END AS classification,
                    coalesce(n.object_type, head([label IN node_labels WHERE label <> 'DataGalaxyObject'])) AS object_type
                RETURN classification, object_type, count(*) AS count
                ORDER BY classification, count DESC, object_type
                """
            )
            orphan_classification_counts: Counter[str] = Counter()
            orphan_counts_by_object_type: dict[str, dict[str, int]] = defaultdict(dict)
            for row in orphan_class_rows:
                classification = str(row["classification"])
                object_type = str(row["object_type"] or "<unknown>")
                count = int(row["count"])
                orphan_classification_counts[classification] += count
                orphan_counts_by_object_type[classification][object_type] = count
            actionable_orphan_count = int(
                orphan_classification_counts.get("catalog_orphan_needs_review", 0)
                + orphan_classification_counts.get("usage_context_needs_review", 0)
                + orphan_classification_counts.get("root_without_parent_metadata", 0)
            )
            sample_rows = session.run(
                """
                MATCH (n:DataGalaxyObject)
                WHERE NOT (n)<-[:CONTAINS|HAS_FIELD]-()
                  AND NOT n:Source
                  AND NOT n:BusinessTerm
                  AND NOT n:DataProcessing
                  AND NOT n:DataProcessingItem
                  AND NOT (
                    n:Usage AND n.parent_node_id IS NULL
                  )
                RETURN
                    n.node_id AS node_id,
                    n.object_type AS object_type,
                    labels(n) AS labels,
                    n.parent_node_id AS parent_node_id,
                    n.name_label AS name_label,
                    n.name_tech AS name_tech,
                    n.path_full AS path_full
                LIMIT 25
                """
            )
            orphan_samples = [dict(row) for row in sample_rows]
            depth_rows = session.run(
                """
                MATCH (n:DataGalaxyObject)
                WHERE n.hierarchy_depth IS NOT NULL
                WITH n.hierarchy_depth AS raw_depth, count(*) AS count
                RETURN toString(raw_depth) AS depth, count
                ORDER BY raw_depth
                """
            )
            hierarchy_depth_distribution = {row["depth"]: int(row["count"]) for row in depth_rows}
        return {
            "total_nodes": int(node_total),
            "total_relationships": int(relationship_total),
            "relationship_counts_by_type": relationship_counts,
            "orphan_count": orphan_count,
            "actionable_orphan_count": actionable_orphan_count,
            "orphan_classification_counts": dict(sorted(orphan_classification_counts.items())),
            "orphan_counts_by_object_type": {
                key: dict(sorted(value.items()))
                for key, value in sorted(orphan_counts_by_object_type.items())
            },
            "orphan_samples": orphan_samples,
            "hierarchy_depth_distribution": hierarchy_depth_distribution,
        }, []
    except BaseException as exc:  # noqa: BLE001
        return {}, [f"neo4j_graph_audit_failed: {exc}"]


def audit_graph(export_id: str, engine: Engine, driver: Any | None = None) -> dict[str, Any]:
    staging = audit_staging_hierarchy(engine, export_id)
    neo4j: dict[str, Any] = {}
    errors: list[str] = []
    if driver is not None:
        neo4j, errors = audit_neo4j_graph(driver)
    return {
        "export_id": export_id,
        "status": "completed" if not errors else "completed_with_warnings",
        "staging_hierarchy": staging,
        "neo4j_graph": neo4j,
        "errors": errors,
    }
