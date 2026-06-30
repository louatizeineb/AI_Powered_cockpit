from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any

from sqlalchemy import text

from app.common.text import normalize_text
from app.db import neo4j_session, postgres_conn


class LineageExplorerRepository:
    # Only semantic relationships are used for lineage expansion.
    # Context relationships are fetched separately to render DataGalaxy-like grouped cards.
    MEANINGFUL_RELATIONSHIP_TYPES = [
        "IS_INPUT_OF",
        "IsInputOf",
        "IS_OUTPUT_OF",
        "IsOutputOf",
        "FLOWS_TO",
        "FlowsTo",
        "USES",
        "IS_USED_BY",
        "IsUsedBy",
        "IS_USAGE_SOURCE_FOR",
        "IS_USAGE_DESTINATION_FOR",
        "HAS_FOR_SOURCE",
        "IS_SOURCE_OF",
        "IS_LINKED_TO",
        "CALLS",
        "IS_CALLED_BY",
        "RESOLVED_TO_SOURCE",
        "PART_OF",
        "PartOf",
    ]

    CONTEXT_RELATIONSHIP_TYPES = [
        "PART_OF", "PartOf", "PROCESSING_CONTEXT",
        "CONTAINS", "HAS_FIELD", "HAS_STRUCTURE", "HAS_CONTAINER",
    ]

    SOURCE_HIERARCHY_RELATIONSHIP_TYPES = [
        "CONTAINS", "HAS_FIELD", "HAS_STRUCTURE", "HAS_CONTAINER",
    ]

    LABEL_PRIORITY = [
        "DataProcessingItem",
        "DataProcessing",
        "Usage",
        "Control",
        "Field",
        "Structure",
        "Table",
        "Dataset",
        "Container",
        "Source",
    ]

    def search_entities(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        started = perf_counter()
        ranked_ids = self._search_indexed_ids(q=q, limit=limit)
        if ranked_ids is None:
            results = self._search_entities_neo4j(q=q, limit=limit)
            self.last_timings = {"neo4j_legacy_search": (perf_counter() - started) * 1000}
            return results

        hydrate_started = perf_counter()
        nodes = self._hydrate_search_nodes(ranked_ids)
        hydrate_ms = (perf_counter() - hydrate_started) * 1000
        flags_started = perf_counter()
        flagged = self._attach_direction_flags(nodes)
        flags_ms = (perf_counter() - flags_started) * 1000
        position = {node_id: index for index, node_id in enumerate(ranked_ids)}
        flagged.sort(key=lambda node: position.get(str(node.get("node_id")), len(position)))
        self.last_timings = {
            "postgres_search": hydrate_started - started,
            "neo4j_hydrate": hydrate_ms,
            "neo4j_flags": flags_ms,
        }
        self.last_timings["postgres_search"] *= 1000
        return flagged[:limit]

    def active_graph_version(self) -> str:
        try:
            with postgres_conn() as conn:
                version = conn.execute(
                    text("""
                    SELECT active_graph_version
                    FROM lineage_search_state
                    WHERE singleton = true
                    """)
                ).scalar()
            return str(version or "legacy")
        except Exception:
            return "legacy"

    def get_node_paths(
        self,
        node_id: str,
        export_id: str | None = None,
        family: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any] | None:
        export_id = export_id or self._latest_migration_v2_export_id()
        if not export_id:
            return None
        params = {
            "export_id": export_id,
            "node_id": node_id,
            "family": family,
            "limit": limit,
        }
        query = text("""
        SELECT
            id,
            export_id,
            graph_version,
            start_node_id,
            end_node_id,
            path_hash,
            path_nodes,
            path_relationships,
            path_length,
            path_family,
            evidence,
            created_at
        FROM lineage_path
        WHERE export_id = :export_id
          AND (:family IS NULL OR path_family = :family)
          AND (
              start_node_id = :node_id
              OR end_node_id = :node_id
              OR path_nodes @> CAST(:node_json AS jsonb)
          )
        ORDER BY path_family, path_length, id
        LIMIT :limit
        """)
        params["node_json"] = json.dumps([{"node_id": node_id}])
        with postgres_conn() as conn:
            rows = conn.execute(query, params).mappings().all()
        return {
            "export_id": export_id,
            "node_id": node_id,
            "family": family,
            "count": len(rows),
            "paths": [self._format_lineage_path_row(row) for row in rows],
        }

    def get_node_audit_context(self, node_id: str, export_id: str | None = None) -> dict[str, Any] | None:
        export_id = export_id or self._latest_migration_v2_export_id()
        if not export_id:
            return None
        with postgres_conn() as conn:
            object_rows = conn.execute(
                text("""
                SELECT node_id, parent_node_id, object_type, name_label, name_tech, path_full,
                       entity_type, data_type, status, app_code, source_table, raw_payload,
                       is_graph_eligible
                FROM catalog_object_staging
                WHERE export_id = :export_id AND node_id = :node_id
                ORDER BY object_type, source_table
                """),
                {"export_id": export_id, "node_id": node_id},
            ).mappings().all()
            finding_rows = conn.execute(
                text("""
                SELECT severity, category, entity_type, node_id, relationship_id, message, evidence, status, created_at
                FROM migration_validation_finding
                WHERE export_id = :export_id
                  AND (
                      node_id = :node_id
                      OR evidence::text LIKE :node_like
                  )
                ORDER BY severity, category, id
                LIMIT 100
                """),
                {"export_id": export_id, "node_id": node_id, "node_like": f"%{node_id}%"},
            ).mappings().all()
            path_counts = conn.execute(
                text("""
                SELECT path_family, count(*) AS count
                FROM lineage_path
                WHERE export_id = :export_id
                  AND (start_node_id = :node_id OR end_node_id = :node_id)
                GROUP BY path_family
                ORDER BY path_family
                """),
                {"export_id": export_id, "node_id": node_id},
            ).mappings().all()
        if not object_rows and not finding_rows and not path_counts:
            return None
        return {
            "export_id": export_id,
            "node_id": node_id,
            "objects": [dict(row) for row in object_rows],
            "validation_findings": [dict(row) for row in finding_rows],
            "path_counts": {row["path_family"]: int(row["count"]) for row in path_counts},
        }

    def _latest_migration_v2_export_id(self) -> str | None:
        try:
            with postgres_conn() as conn:
                value = conn.execute(
                    text("""
                    SELECT export_id
                    FROM migration_export_run
                    ORDER BY created_at DESC
                    LIMIT 1
                    """)
                ).scalar()
            return str(value) if value else None
        except Exception:
            return None

    def _format_lineage_path_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "export_id": row["export_id"],
            "graph_version": row["graph_version"],
            "start_node_id": row["start_node_id"],
            "end_node_id": row["end_node_id"],
            "path_hash": row["path_hash"],
            "path_nodes": row["path_nodes"],
            "path_relationships": row["path_relationships"],
            "path_length": row["path_length"],
            "path_family": row["path_family"],
            "evidence": row["evidence"],
            "created_at": row["created_at"],
        }

    def _search_indexed_ids(self, q: str, limit: int) -> list[str] | None:
        needle = normalize_text(q) or ""
        needle_words = needle.replace("_", " ")
        looks_like_path = any(separator in q for separator in ["\\", "/", ">", "|"])
        fetch_limit = min(max(limit * 5, 30), 150)
        fast_query = text("""
        WITH candidates AS (
            (SELECT node_id, entity_level, 0 AS rank_group, 1.0::real AS match_score
             FROM lineage_search_document WHERE node_id = :raw_q
             LIMIT :branch_limit)
            UNION ALL
            (SELECT node_id, entity_level, 1, 1.0::real
             FROM lineage_search_document WHERE normalized_label = :needle
             LIMIT :branch_limit)
            UNION ALL
            (SELECT node_id, entity_level, 1, 1.0::real
             FROM lineage_search_document WHERE normalized_technical_name = :needle
             LIMIT :branch_limit)
            UNION ALL
            (SELECT node_id, entity_level, 2, 0.9::real
             FROM lineage_search_document WHERE normalized_label LIKE :prefix
             LIMIT :branch_limit)
            UNION ALL
            (SELECT node_id, entity_level, 2, 0.9::real
             FROM lineage_search_document WHERE normalized_technical_name LIKE :prefix
             LIMIT :branch_limit)
            UNION ALL
            (SELECT node_id, entity_level, 3,
                    ts_rank(search_tsv, to_tsquery('simple', :ts_prefix)) AS match_score
             FROM lineage_search_document
             WHERE search_tsv @@ to_tsquery('simple', :ts_prefix)
             LIMIT :branch_limit)
        ),
        best AS (
            SELECT node_id, entity_level, min(rank_group) AS rank_group, max(match_score) AS match_score
            FROM candidates
            GROUP BY node_id, entity_level
        ),
        ranked AS (
            SELECT DISTINCT ON (node_id) node_id, entity_level, rank_group, match_score
            FROM best
            ORDER BY node_id, rank_group, match_score DESC
        )
        SELECT node_id
        FROM ranked
        ORDER BY rank_group, match_score DESC, node_id
        LIMIT :limit
        """)
        path_query = text("""
        SELECT DISTINCT node_id
        FROM lineage_search_document
        WHERE normalized_path LIKE :contains
        LIMIT :limit
        """)
        fuzzy_query = text("""
        WITH candidates AS (
            (SELECT node_id, entity_level, similarity(normalized_label, :needle) AS match_score
             FROM lineage_search_document WHERE normalized_label % :needle
             LIMIT :branch_limit)
            UNION ALL
            (SELECT node_id, entity_level, similarity(normalized_technical_name, :needle)
             FROM lineage_search_document WHERE normalized_technical_name % :needle
             LIMIT :branch_limit)
        )
        SELECT node_id
        FROM candidates
        GROUP BY node_id
        ORDER BY max(match_score) DESC
        LIMIT :limit
        """)
        try:
            with postgres_conn() as conn:
                version = conn.execute(
                    text("""
                    SELECT active_graph_version
                    FROM lineage_search_state
                    WHERE singleton = true
                    """)
                ).scalar()
                if not version:
                    return None
                params = {
                    "raw_q": q.strip(),
                    "needle": needle,
                    "needle_words": needle_words,
                    "ts_prefix": " & ".join(f"{token}:*" for token in needle_words.split()),
                    "prefix": f"{needle}%",
                    "contains": f"%{needle}%",
                    "branch_limit": fetch_limit,
                    "limit": fetch_limit,
                }
                if looks_like_path:
                    rows = conn.execute(path_query, params).scalars().all()
                else:
                    rows = conn.execute(fast_query, params).scalars().all()
                    if not rows and len(needle) >= 3:
                        rows = conn.execute(fuzzy_query, params).scalars().all()
            return [str(node_id) for node_id in rows]
        except Exception:
            return None

    def _hydrate_search_nodes(self, node_ids: list[str]) -> list[dict[str, Any]]:
        if not node_ids:
            return []
        query = """
        UNWIND $node_ids AS node_id
        CALL (node_id) {
            MATCH (n:DataGalaxyObject {node_id: node_id})
            RETURN n
            LIMIT 1
            UNION
            MATCH (n:Usage {usage_uuid: node_id})
            RETURN n
            LIMIT 1
            UNION
            MATCH (n:Usage {node_id: node_id})
            RETURN n
            LIMIT 1
        }
        RETURN n
        """
        with neo4j_session() as session:
            records = list(session.run(query, node_ids=node_ids))
        by_id = {
            self._raw_identifier(record["n"]): self._format_node(record["n"])
            for record in records
            if record["n"] is not None
        }
        return [by_id[node_id] for node_id in node_ids if node_id in by_id]

    def _search_entities_neo4j(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        needle = q.strip().lower()
        looks_like_path = any(separator in needle for separator in ["\\", "/", ">", "|"])
        fetch_limit = min(max(limit * (2 if looks_like_path else 5), 20), 60 if looks_like_path else 150)
        path_query = """
        CALL () {
            MATCH (n:Field)
            WHERE toLower(toString(coalesce(n.path_full, ''))) CONTAINS $needle
            RETURN n, 1 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Structure)
            WHERE toLower(toString(coalesce(n.path_full, ''))) CONTAINS $needle
            RETURN n, 2 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Usage)
            WHERE toLower(toString(coalesce(n.usage_path, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.path_full, ''))) CONTAINS $needle
            RETURN n, 3 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Source)
            WHERE toLower(toString(coalesce(n.path_full, ''))) CONTAINS $needle
            RETURN n, 4 AS rank
            LIMIT $limit
        }
        WITH n, min(rank) AS rank
        ORDER BY rank,
             coalesce(n.path_full, n.usage_path, n.name_label, n.usage_name, n.name_tech, n.name)
        RETURN collect(n) AS nodes
        """
        exact_query = """
        CALL () {
            MATCH (n:DataGalaxyObject)
            WHERE n.node_id = $raw_q
            RETURN n, 0 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Usage)
            WHERE n.usage_uuid = $raw_q OR n.node_id = $raw_q
            RETURN n, 0 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Source)
            WHERE n.node_id = $raw_q
            RETURN n, 0 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Field)
            WHERE toLower(toString(coalesce(n.name_tech, ''))) = $needle
               OR toLower(toString(coalesce(n.name, ''))) = $needle
               OR toLower(toString(coalesce(n.name_label, ''))) = $needle
            RETURN n, 1 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Structure)
            WHERE toLower(toString(coalesce(n.name_tech, ''))) = $needle
               OR toLower(toString(coalesce(n.name, ''))) = $needle
               OR toLower(toString(coalesce(n.name_label, ''))) = $needle
            RETURN n, 1 AS rank
            LIMIT $limit
            UNION
            MATCH (n:DataProcessing)
            WHERE toLower(toString(coalesce(n.name_tech, ''))) = $needle
               OR toLower(toString(coalesce(n.name, ''))) = $needle
               OR toLower(toString(coalesce(n.name_label, ''))) = $needle
            RETURN n, 1 AS rank
            LIMIT $limit
            UNION
            MATCH (n:DataProcessingItem)
            WHERE toLower(toString(coalesce(n.name_tech, ''))) = $needle
               OR toLower(toString(coalesce(n.name, ''))) = $needle
               OR toLower(toString(coalesce(n.name_label, ''))) = $needle
            RETURN n, 1 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Usage)
            WHERE toLower(toString(coalesce(n.usage_name, ''))) = $needle
               OR toLower(toString(coalesce(n.usage_tech_name, ''))) = $needle
               OR toLower(toString(coalesce(n.name_label, ''))) = $needle
            RETURN n, 1 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Source)
            WHERE toLower(toString(coalesce(n.name_tech, ''))) = $needle
               OR toLower(toString(coalesce(n.name, ''))) = $needle
               OR toLower(toString(coalesce(n.name_label, ''))) = $needle
            RETURN n, 1 AS rank
            LIMIT $limit
        }
        WITH n, min(rank) AS rank
        ORDER BY rank,
             CASE
                WHEN n:Field THEN 0
                WHEN n:Structure THEN 1
                WHEN n:DataProcessingItem THEN 2
                WHEN n:DataProcessing THEN 3
                WHEN n:Usage THEN 4
                WHEN n:Source THEN 5
                ELSE 6
             END,
             coalesce(n.path_full, n.usage_path, n.name_label, n.usage_name, n.name_tech, n.name)
        RETURN collect(n) AS nodes
        """
        partial_query = """
        CALL () {
            MATCH (n:Field)
            WHERE toLower(toString(coalesce(n.name_tech, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.name, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.name_label, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.path_full, ''))) CONTAINS $needle
            RETURN n, 2 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Structure)
            WHERE toLower(toString(coalesce(n.name_tech, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.name, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.name_label, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.path_full, ''))) CONTAINS $needle
            RETURN n, 3 AS rank
            LIMIT $limit
            UNION
            MATCH (n:DataProcessing)
            WHERE toLower(toString(coalesce(n.name_tech, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.name, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.name_label, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.path, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.path_full, ''))) CONTAINS $needle
            RETURN n, 4 AS rank
            LIMIT $limit
            UNION
            MATCH (n:DataProcessingItem)
            WHERE toLower(toString(coalesce(n.name_tech, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.name, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.name_label, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.path, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.path_full, ''))) CONTAINS $needle
            RETURN n, 4 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Usage)
            WHERE toLower(toString(coalesce(n.usage_name, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.usage_tech_name, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.name_label, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.usage_path, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.path_full, ''))) CONTAINS $needle
            RETURN n, 5 AS rank
            LIMIT $limit
            UNION
            MATCH (n:Source)
            WHERE toLower(toString(coalesce(n.name_tech, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.name, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.name_label, ''))) CONTAINS $needle
               OR toLower(toString(coalesce(n.path_full, ''))) CONTAINS $needle
            RETURN n, 6 AS rank
            LIMIT $limit
        }
        WITH n, min(rank) AS rank
        WHERE NOT (coalesce(n.node_id, n.usage_uuid, elementId(n)) IN $seen_ids)
        ORDER BY rank,
             CASE
                WHEN n:Field THEN 0
                WHEN n:Structure THEN 1
                WHEN n:DataProcessingItem THEN 2
                WHEN n:DataProcessing THEN 3
                WHEN n:Usage THEN 4
                WHEN n:Source THEN 5
                ELSE 6
             END,
             coalesce(n.path_full, n.usage_path, n.name_label, n.usage_name, n.name_tech, n.name)
        RETURN collect(n) AS nodes
        """
        if looks_like_path:
            with neo4j_session() as session:
                record = session.run(path_query, needle=needle, limit=fetch_limit).single()
            exact_nodes = list(record["nodes"] if record else [])
        else:
            with neo4j_session() as session:
                record = session.run(
                    exact_query,
                    raw_q=q.strip(),
                    needle=needle,
                    limit=fetch_limit,
                ).single()

            exact_nodes = list(record["nodes"] if record else [])
            if len(exact_nodes) < fetch_limit:
                seen_ids = [self._raw_identifier(node) for node in exact_nodes]
                with neo4j_session() as session:
                    partial_record = session.run(
                        partial_query,
                        needle=needle,
                        seen_ids=seen_ids,
                        limit=fetch_limit - len(exact_nodes),
                    ).single()
                exact_nodes.extend(partial_record["nodes"] if partial_record else [])

        nodes = [self._format_node(node) for node in exact_nodes]
        flagged = self._attach_direction_flags(nodes)
        flagged.sort(
            key=lambda node: (
                self._search_relevance(node, needle, q.strip()),
                not (node.get("has_upstream") or node.get("has_downstream")),
                self._category_rank(node.get("category")),
                str(node.get("path") or node.get("label") or ""),
            )
        )
        return flagged[:limit]

    def _search_relevance(self, node: dict[str, Any], needle: str, raw_q: str) -> int:
        values = [
            node.get("node_id"),
            node.get("label"),
            node.get("technical_name"),
            node.get("path"),
        ]
        normalized = [str(value or "").strip().lower() for value in values]
        if str(node.get("node_id") or "") == raw_q:
            return 0
        if needle in normalized:
            return 0
        if any(value.endswith(f"\\{needle}") or value.endswith(f"/{needle}") for value in normalized):
            return 1
        return 2

    def get_neighbors(self, node_id: str, direction: str, limit: int = 50) -> dict[str, Any] | None:
        query = """
        CALL () {
            MATCH (center:DataGalaxyObject {node_id: $node_id})
            RETURN center
            LIMIT 1
            UNION
            MATCH (center:Usage {usage_uuid: $node_id})
            RETURN center
            LIMIT 1
            UNION
            MATCH (center:Usage {node_id: $node_id})
            RETURN center
            LIMIT 1
        }
        WITH center
        LIMIT 1
        CALL (center) {
            MATCH (center)-[r]-(neighbor)
            WHERE type(r) IN $relationship_types
            RETURN collect(DISTINCT {rel: r, neighbor: neighbor}) AS items
        }
        CALL (center, items) {
            WITH [center] + [item IN items | item.neighbor] AS candidates
            UNWIND candidates AS neighbor
            OPTIONAL MATCH (neighbor)-[context_rel]-(context)
            WHERE type(context_rel) IN $context_relationship_types
              AND (
                (neighbor:DataProcessingItem AND context:DataProcessing)
                OR (neighbor:Field AND (context:Structure OR context:Table OR context:Dataset OR context:Source OR context:Container))
                OR (
                  (neighbor:Structure OR neighbor:Table OR neighbor:Dataset OR neighbor:Container)
                  AND (context:Structure OR context:Table OR context:Dataset OR context:Source OR context:Container)
                )
                OR (neighbor:Usage AND context:Usage)
              )
            RETURN collect(DISTINCT {rel: context_rel, node: context, child: neighbor}) AS context_items
        }
        RETURN center, items, context_items
        """
        with neo4j_session() as session:
            record = session.run(
                query,
                node_id=node_id,
                relationship_types=self.MEANINGFUL_RELATIONSHIP_TYPES,
                context_relationship_types=self.CONTEXT_RELATIONSHIP_TYPES,
            ).single()

        if record is None or record["center"] is None:
            return None

        center_node = record["center"]
        center_raw_id = self._raw_identifier(center_node)
        formatted_center = self._format_node(center_node)
        node_by_id: dict[str, dict[str, Any]] = {}
        edge_by_id: dict[str, dict[str, Any]] = {}

        for item in record["items"] or []:
            rel = item.get("rel")
            neighbor = item.get("neighbor")
            if rel is None or neighbor is None or not self._should_include_relationship(rel):
                continue

            oriented = self._orient_relationship(rel)
            if oriented is None:
                continue

            is_downstream = oriented["source_raw"] == center_raw_id
            is_upstream = oriented["target_raw"] == center_raw_id
            if direction == "downstream" and not is_downstream:
                continue
            if direction == "upstream" and not is_upstream:
                continue

            formatted_neighbor = self._format_node(neighbor)
            node_by_id[formatted_neighbor["id"]] = formatted_neighbor

            edge_direction = "downstream" if is_downstream else "upstream"
            edge = {
                "id": f"edge:{str(rel.element_id)}",
                "source": oriented["source"],
                "target": oriented["target"],
                "raw_source": oriented["raw_source"],
                "raw_target": oriented["raw_target"],
                "type": oriented["type"],
                "raw_type": oriented["raw_type"],
                "direction": edge_direction,
                "visual_source": oriented["source"],
                "visual_target": oriented["target"],
                "is_visual_reversed": oriented["is_visual_reversed"],
                "properties": oriented["properties"],
            }
            edge_by_id[edge["id"]] = edge

            if len(node_by_id) >= limit:
                break

        for item in record["context_items"] or []:
            rel = item.get("rel")
            context = item.get("node")
            if rel is None or context is None:
                continue
            formatted_context = self._format_node(context)
            node_by_id.setdefault(formatted_context["id"], formatted_context)
            oriented = self._orient_relationship(rel)
            if oriented is None:
                continue
            edge_by_id.setdefault(
                f"edge:{str(rel.element_id)}",
                {
                    "id": f"edge:{str(rel.element_id)}",
                    "source": oriented["source"],
                    "target": oriented["target"],
                    "raw_source": oriented["raw_source"],
                    "raw_target": oriented["raw_target"],
                    "type": oriented["type"],
                    "raw_type": oriented["raw_type"],
                    "direction": direction,
                    "visual_source": oriented["source"],
                    "visual_target": oriented["target"],
                    "is_visual_reversed": oriented["is_visual_reversed"],
                    "properties": oriented["properties"],
                },
            )

        self._add_usage_parent_contexts(node_by_id, edge_by_id, [formatted_center])

        flagged = self._attach_direction_flags([formatted_center, *node_by_id.values()])
        flagged_by_id = {node["id"]: node for node in flagged}
        return {
            "center": flagged_by_id.get(formatted_center["id"], formatted_center),
            "nodes": [flagged_by_id.get(node["id"], node) for node in node_by_id.values()],
            "edges": list(edge_by_id.values()),
        }

    def get_source_context(
        self,
        node_id: str,
        catalog_offset: int = 0,
        catalog_limit: int = 500,
        consumer_limit: int = 300,
    ) -> dict[str, Any] | None:
        source_row, catalog_rows = self._list_source_catalog_rows(
            node_id=node_id,
            catalog_offset=catalog_offset,
            catalog_limit=catalog_limit,
        )
        if source_row is None:
            return None

        consumer_query = """
        UNWIND $node_ids AS node_id
        MATCH (catalog:DataGalaxyObject {node_id: node_id})-[rel]-(consumer)
        WHERE type(rel) IN $relationship_types
          AND (consumer:Usage OR consumer:DataProcessingItem OR consumer:DataProcessing)
        WITH DISTINCT catalog, rel, consumer
        LIMIT $consumer_limit
        RETURN collect({catalog: catalog, rel: rel, neighbor: consumer}) AS consumer_items
        """
        with neo4j_session() as session:
            record = session.run(
                consumer_query,
                node_ids=[node_id, *[row["node_id"] for row in catalog_rows]],
                consumer_limit=consumer_limit,
                relationship_types=self.MEANINGFUL_RELATIONSHIP_TYPES,
            ).single()

        formatted_center = self._format_catalog_row(source_row)
        node_by_id: dict[str, dict[str, Any]] = {}
        edge_by_id: dict[str, dict[str, Any]] = {}

        for row in catalog_rows:
            formatted = self._format_catalog_row(row)
            node_by_id[formatted["id"]] = formatted
            edge = self._format_catalog_edge(row)
            edge_by_id[edge["id"]] = edge

        catalog_count = len(catalog_rows)
        formatted_center["properties"] = {
            **formatted_center["properties"],
            "source_context_catalog_offset": catalog_offset,
            "source_context_catalog_count": catalog_count,
            "source_context_next_offset": catalog_offset + catalog_count,
            "source_context_has_more": catalog_count >= catalog_limit,
        }

        for item in (record["consumer_items"] if record else []) or []:
            rel = item.get("rel")
            neighbor = item.get("neighbor")
            if rel is None or neighbor is None:
                continue
            formatted = self._format_node(neighbor)
            node_by_id[formatted["id"]] = formatted
            self._add_formatted_edge(edge_by_id, rel, "downstream")

        self._add_usage_parent_contexts(node_by_id, edge_by_id)

        flagged = self._apply_edge_flags([formatted_center, *node_by_id.values()], list(edge_by_id.values()))
        flagged_by_id = {node["id"]: node for node in flagged}
        return {
            "center": flagged_by_id.get(formatted_center["id"], formatted_center),
            "nodes": [flagged_by_id.get(node["id"], node) for node in node_by_id.values()],
            "edges": list(edge_by_id.values()),
        }

    def _add_usage_parent_contexts(
        self,
        node_by_id: dict[str, dict[str, Any]],
        edge_by_id: dict[str, dict[str, Any]],
        additional_nodes: list[dict[str, Any]] | None = None,
    ) -> None:
        usage_ids = [
            node["node_id"]
            for node in [*node_by_id.values(), *(additional_nodes or [])]
            if node.get("category") == "usage"
        ]
        if not usage_ids:
            return

        query = """
        UNWIND $usage_ids AS usage_id
        MATCH (child:Usage {usage_uuid: usage_id})<-[rel:CONTAINS]-(parent:Usage)
        RETURN DISTINCT child, parent, rel
        """
        with neo4j_session() as session:
            records = list(session.run(query, usage_ids=usage_ids))

        for record in records:
            parent = record["parent"]
            rel = record["rel"]
            formatted_parent = self._format_node(parent)
            node_by_id.setdefault(formatted_parent["id"], formatted_parent)
            self._add_formatted_edge(edge_by_id, rel, "downstream")

    def _list_source_catalog_rows(
        self,
        node_id: str,
        catalog_offset: int,
        catalog_limit: int,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        child_query = text("""
        SELECT 'container' AS catalog_kind, node_id, parent_node_id, name_label, name_tech,
               path_full, path_type, entity_type, data_type, children_count::text AS children_count
        FROM container
        WHERE parent_node_id = ANY(:parent_ids)
          AND node_id IS NOT NULL
          AND node_id <> ''
        UNION ALL
        SELECT 'structure' AS catalog_kind, node_id, parent_node_id, name_label, name_tech,
               path_full, path_type, entity_type, data_type, children_count::text AS children_count
        FROM structure
        WHERE parent_node_id = ANY(:parent_ids)
          AND node_id IS NOT NULL
          AND node_id <> ''
        UNION ALL
        SELECT 'field' AS catalog_kind, node_id, parent_node_id, name_label, name_tech,
               path_full, path_type, entity_type, data_type, children_count::text AS children_count
        FROM field
        WHERE parent_node_id = ANY(:parent_ids)
          AND node_id IS NOT NULL
          AND node_id <> ''
        ORDER BY path_full NULLS LAST, name_label NULLS LAST, node_id
        LIMIT :remaining
        """)
        needed = catalog_offset + catalog_limit
        discovered: list[dict[str, Any]] = []
        parent_ids = [node_id]
        parent_kinds = {node_id: "source"}

        with postgres_conn() as conn:
            source = conn.execute(
                text("SELECT * FROM source WHERE node_id = :node_id LIMIT 1"),
                {"node_id": node_id},
            ).mappings().first()
            if source is None:
                return None, []

            for _depth in range(8):
                if not parent_ids or len(discovered) >= needed:
                    break
                rows = conn.execute(
                    child_query,
                    {
                        "parent_ids": parent_ids,
                        "remaining": needed - len(discovered),
                    },
                ).mappings().all()
                if not rows:
                    break
                next_parent_ids: list[str] = []
                for raw_row in rows:
                    row = dict(raw_row)
                    row["parent_catalog_kind"] = parent_kinds.get(str(row["parent_node_id"]), "asset")
                    discovered.append(row)
                    if row["catalog_kind"] != "field":
                        next_parent_ids.append(str(row["node_id"]))
                        parent_kinds[str(row["node_id"])] = str(row["catalog_kind"])
                parent_ids = next_parent_ids

        return dict(source), discovered[catalog_offset:needed]

    def _format_catalog_row(self, row: dict[str, Any]) -> dict[str, Any]:
        kind = str(row.get("catalog_kind") or "source")
        node_id = str(row.get("node_id") or "")
        label = str(row.get("name_label") or row.get("name_tech") or node_id)
        category = {
            "source": "source",
            "structure": "structure",
            "field": "field",
        }.get(kind, "asset")
        display_type = {
            "source": "Source",
            "container": "Container",
            "structure": "Structure",
            "field": "Field",
        }.get(kind, "Asset")
        path = row.get("path_full")
        parent_node_id = str(row.get("parent_node_id") or "") or None
        return {
            "id": self._catalog_graph_id(kind, node_id),
            "node_id": node_id,
            "label": label,
            "technical_name": str(row.get("name_tech")) if row.get("name_tech") is not None else None,
            "type": display_type,
            "category": category,
            "entity_type": str(row.get("entity_type")) if row.get("entity_type") is not None else None,
            "data_type": str(row.get("data_type")) if row.get("data_type") is not None else None,
            "path_full": str(path) if path is not None else None,
            "path_type": str(row.get("path_type")) if row.get("path_type") is not None else None,
            "parent_node_id": parent_node_id,
            "parent_label": self._parent_label(str(path) if path is not None else None, row),
            "parent_type": None,
            "path": str(path) if path is not None else None,
            "visual_role": self._visual_role(category),
            "group_id": parent_node_id or self._catalog_graph_id(kind, node_id),
            "group_type": self._group_type(category),
            "group_label": label,
            "has_upstream": False,
            "has_downstream": False,
            "properties": self._json_safe(dict(row)),
        }

    def _format_catalog_edge(self, row: dict[str, Any]) -> dict[str, Any]:
        parent_id = str(row["parent_node_id"])
        child_id = str(row["node_id"])
        parent_kind = str(row.get("parent_catalog_kind") or "asset")
        child_kind = str(row.get("catalog_kind") or "asset")
        relationship = "HAS_FIELD" if child_kind == "field" else "CONTAINS"
        source = self._catalog_graph_id(parent_kind, parent_id)
        target = self._catalog_graph_id(child_kind, child_id)
        return {
            "id": f"catalog-edge:{parent_id}:{child_id}",
            "source": source,
            "target": target,
            "raw_source": source,
            "raw_target": target,
            "type": relationship,
            "raw_type": relationship,
            "direction": "downstream",
            "visual_source": source,
            "visual_target": target,
            "is_visual_reversed": False,
            "properties": {"source": "postgres_catalog"},
        }

    def _catalog_graph_id(self, kind: str, node_id: str) -> str:
        prefix = {
            "source": "source",
            "container": "asset",
            "structure": "structure",
            "field": "field",
        }.get(kind, "asset")
        return f"{prefix}:{node_id}"

    def _apply_edge_flags(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {node["id"]: {**node} for node in nodes}
        for edge in edges:
            source = by_id.get(edge["source"])
            target = by_id.get(edge["target"])
            if source is not None:
                source["has_downstream"] = True
            if target is not None:
                target["has_upstream"] = True
        return list(by_id.values())

    def _add_formatted_edge(self, edge_by_id: dict[str, dict[str, Any]], rel, direction: str) -> None:
        oriented = self._orient_relationship(rel)
        if oriented is None:
            return
        edge_id = f"edge:{str(rel.element_id)}"
        edge_by_id.setdefault(
            edge_id,
            {
                "id": edge_id,
                "source": oriented["source"],
                "target": oriented["target"],
                "raw_source": oriented["raw_source"],
                "raw_target": oriented["raw_target"],
                "type": oriented["type"],
                "raw_type": oriented["raw_type"],
                "direction": direction,
                "visual_source": oriented["source"],
                "visual_target": oriented["target"],
                "is_visual_reversed": oriented["is_visual_reversed"],
                "properties": oriented["properties"],
            },
        )

    def _attach_direction_flags(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not nodes:
            return []

        identifiers = sorted({node["node_id"] for node in nodes if node.get("node_id")})
        query = """
        CALL () {
            UNWIND $identifiers AS identifier
            MATCH (n:DataGalaxyObject {node_id: identifier})
            RETURN n
            UNION
            UNWIND $identifiers AS identifier
            MATCH (n:Usage {usage_uuid: identifier})
            RETURN n
            UNION
            UNWIND $identifiers AS identifier
            MATCH (n:Usage {node_id: identifier})
            RETURN n
        }
        WITH DISTINCT n
        CALL (n) {
            MATCH (n)-[r]-(neighbor)
            WHERE type(r) IN $relationship_types
            RETURN collect(DISTINCT r) AS rels
        }
        RETURN n, rels
        """
        flags = {node["node_id"]: {"has_upstream": False, "has_downstream": False} for node in nodes}

        with neo4j_session() as session:
            records = list(session.run(
                query,
                identifiers=identifiers,
                relationship_types=self.MEANINGFUL_RELATIONSHIP_TYPES,
            ))

        for record in records:
            node = record["n"]
            raw_id = self._raw_identifier(node)
            node_flags = flags.setdefault(raw_id, {"has_upstream": False, "has_downstream": False})
            for rel in record["rels"] or []:
                if not self._should_include_relationship(rel):
                    continue
                oriented = self._orient_relationship(rel)
                if oriented is None:
                    continue
                if oriented["source_raw"] == raw_id:
                    node_flags["has_downstream"] = True
                if oriented["target_raw"] == raw_id:
                    node_flags["has_upstream"] = True

        flagged = [{**node, **flags.get(node["node_id"], {})} for node in nodes]
        return [
            {
                **node,
                "has_upstream": bool(node.get("has_upstream") or self._has_parent_context(node)),
            }
            for node in flagged
        ]

    @staticmethod
    def _has_parent_context(node: dict[str, Any]) -> bool:
        return bool(
            node.get("parent_node_id")
            and node.get("category") in {"field", "structure", "dataset", "asset", "processing_item", "usage"}
        )

    def _format_node(self, node) -> dict[str, Any]:
        props = dict(node)
        labels = list(node.labels)
        node_id = self._raw_identifier(node)
        preferred_label = self._preferred_label(labels)
        category = self._category(preferred_label, props)
        label = (
            props.get("usage_name")
            or props.get("name_label")
            or props.get("name")
            or props.get("name_tech")
            or props.get("usage_tech_name")
            or node_id
        )
        technical_name = (
            props.get("usage_tech_name")
            or props.get("name_tech")
            or props.get("technical_name")
            or props.get("name")
        )
        path = props.get("usage_path") or props.get("path_full") or props.get("path") or props.get("technical_path")
        parent_node_id = self._parent_identifier(props)
        parent_label = self._parent_label(path, props)
        parent_type = str(props.get("parent_type") or props.get("parent_entity_type") or "") or None
        clean_props = self._json_safe({**props, "labels": labels})
        group_type = self._group_type(category)
        group_label = parent_label if category in {"field", "processing_item"} else str(label)
        return {
            "id": self._graph_id(node),
            "node_id": node_id,
            "label": str(label),
            "technical_name": str(technical_name) if technical_name is not None else None,
            "type": self._display_type(preferred_label, props),
            "category": category,
            "entity_type": str(props.get("entity_type")) if props.get("entity_type") is not None else None,
            "data_type": str(props.get("data_type")) if props.get("data_type") is not None else None,
            "path_full": str(props.get("path_full")) if props.get("path_full") is not None else None,
            "path_type": str(props.get("path_type")) if props.get("path_type") is not None else None,
            "parent_node_id": parent_node_id,
            "parent_label": parent_label,
            "parent_type": parent_type,
            "path": path,
            "visual_role": self._visual_role(category),
            "group_id": parent_node_id or self._graph_id(node),
            "group_type": group_type,
            "group_label": group_label,
            "has_upstream": False,
            "has_downstream": False,
            "properties": clean_props,
        }

    def _orient_relationship(self, rel) -> dict[str, Any] | None:
        if not hasattr(rel, "type") or not hasattr(rel, "start_node") or not hasattr(rel, "end_node"):
            return None
        if not self._should_include_relationship(rel):
            return None

        raw_rel_type = str(rel.type)
        rel_type = self._canonical_relationship_type(raw_rel_type)
        start = rel.start_node
        end = rel.end_node
        start_id = self._raw_identifier(start)
        end_id = self._raw_identifier(end)
        start_kind = self._category(self._preferred_label(list(start.labels)), dict(start))
        end_kind = self._category(self._preferred_label(list(end.labels)), dict(end))
        source = start
        target = end
        display_type = rel_type
        flow_direction = "stored"
        is_visual_reversed = False

        if rel_type == "IS_OUTPUT_OF":
            source, target = end, start
            flow_direction = "reversed_output"
            is_visual_reversed = True
        elif rel_type == "IS_CALLED_BY":
            source, target = end, start
            flow_direction = "reversed_called_by"
            is_visual_reversed = True
        elif rel_type in {"USES", "IS_USED_BY", "HAS_FOR_SOURCE", "IS_SOURCE_OF", "IS_USAGE_SOURCE_FOR", "IS_USAGE_DESTINATION_FOR", "RESOLVED_TO_SOURCE"}:
            if start_kind == "usage" and end_kind != "usage":
                source, target = end, start
                flow_direction = "asset_to_usage"
            elif end_kind == "usage" and start_kind != "usage":
                source, target = start, end
                flow_direction = "asset_to_usage"
            elif start_kind == "usage" and end_kind == "usage":
                source, target = self._orient_usage_dependency(start, end)
                flow_direction = "usage_dependency"
                display_type = "USAGE_DEPENDS_ON"
        elif rel_type in {"PART_OF", "PROCESSING_CONTEXT", "HAS_FIELD", "CONTAINS", "HAS_STRUCTURE", "HAS_CONTAINER"}:
            source, target = start, end
            display_type = "PROCESSING_CONTEXT" if rel_type in {"PART_OF", "PROCESSING_CONTEXT"} else rel_type

        source_id = self._raw_identifier(source)
        target_id = self._raw_identifier(target)
        return {
            "source_raw": source_id,
            "target_raw": target_id,
            "raw_source": self._graph_id(start),
            "raw_target": self._graph_id(end),
            "source": self._graph_id(source),
            "target": self._graph_id(target),
            "type": display_type,
            "raw_type": raw_rel_type,
            "is_visual_reversed": is_visual_reversed,
            "properties": {
                **self._json_safe(dict(rel)),
                "raw_type": raw_rel_type,
                "storage_source": start_id,
                "storage_target": end_id,
                "flow_direction": flow_direction,
            },
        }

    def _orient_usage_dependency(self, start, end):
        start_id = self._raw_identifier(start)
        end_id = self._raw_identifier(end)
        start_parent = self._parent_identifier(dict(start))
        end_parent = self._parent_identifier(dict(end))
        if end_parent == start_id:
            return start, end
        if start_parent == end_id:
            return end, start
        return start, end

    def _should_include_relationship(self, rel) -> bool:
        if not hasattr(rel, "type") or not hasattr(rel, "start_node") or not hasattr(rel, "end_node"):
            return False
        rel_type = self._canonical_relationship_type(str(rel.type))
        if rel_type not in self.MEANINGFUL_RELATIONSHIP_TYPES and rel_type not in {"PROCESSING_CONTEXT", "HAS_FIELD", "CONTAINS", "HAS_STRUCTURE", "HAS_CONTAINER"}:
            return False
        if rel_type in {"PROCESSING_CONTEXT", "HAS_FIELD", "CONTAINS", "HAS_STRUCTURE", "HAS_CONTAINER"}:
            return True
        if rel_type != "PART_OF":
            return True
        kinds = {
            self._category(self._preferred_label(list(rel.start_node.labels)), dict(rel.start_node)),
            self._category(self._preferred_label(list(rel.end_node.labels)), dict(rel.end_node)),
        }
        return kinds == {"processing", "processing_item"}

    def _canonical_relationship_type(self, value: str) -> str:
        compact = re.sub(r"[\s_-]+", "", str(value or "")).upper()
        mapping = {
            "ISINPUTOF": "IS_INPUT_OF",
            "ISOUTPUTOF": "IS_OUTPUT_OF",
            "FLOWSTO": "FLOWS_TO",
            "ISUSEDBY": "IS_USED_BY",
            "ISUSAGESOURCEFOR": "IS_USAGE_SOURCE_FOR",
            "ISUSAGEDESTINATIONFOR": "IS_USAGE_DESTINATION_FOR",
            "HASFORSOURCE": "HAS_FOR_SOURCE",
            "ISSOURCEOF": "IS_SOURCE_OF",
            "ISLINKEDTO": "IS_LINKED_TO",
            "ISCALLEDBY": "IS_CALLED_BY",
            "RESOLVEDTOSOURCE": "RESOLVED_TO_SOURCE",
            "PARTOF": "PART_OF",
            "PROCESSINGCONTEXT": "PROCESSING_CONTEXT",
            "CONTAINS": "CONTAINS",
            "HASFIELD": "HAS_FIELD",
            "HASSTRUCTURE": "HAS_STRUCTURE",
            "HASCONTAINER": "HAS_CONTAINER",
        }
        return mapping.get(compact, str(value or "").upper())

    def _json_safe(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, dict):
            return {str(key): self._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe(item) for item in value]
        if hasattr(value, "iso_format"):
            return value.iso_format()
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    def _preferred_label(self, labels: list[str]) -> str:
        label_lookup = {label.lower(): label for label in labels}
        for preferred in self.LABEL_PRIORITY:
            if preferred.lower() in label_lookup:
                return label_lookup[preferred.lower()]
        return labels[0] if labels else "Node"

    def _display_type(self, label: str, props: dict[str, Any]) -> str:
        category = self._category(label, props)
        if category == "processing":
            return "DataProcessing"
        if category == "processing_item":
            return "DataProcessingItem"
        if category == "dataset":
            return "Dataset"
        if category == "usage":
            return "Usage"
        if category == "control":
            return "Control"
        if category == "field":
            return "Field"
        if category == "structure":
            return "Structure"
        if category == "source":
            return "Source"
        return label or "Node"

    def _visual_role(self, category: str) -> str:
        return {
            "source": "golden_source",
            "processing": "data_processing",
            "processing_item": "data_processing_item",
            "usage": "usage",
            "field": "intermediate_asset",
            "structure": "intermediate_asset",
            "dataset": "intermediate_asset",
        }.get(category, "intermediate_asset")

    def _group_type(self, category: str) -> str:
        if category == "processing" or category == "processing_item":
            return "data_processing"
        if category == "usage":
            return "usage"
        if category in {"source", "structure", "field"}:
            return "source_table"
        return "dataset"

    def _category(self, label: str, props: dict[str, Any]) -> str:
        value = " ".join(
            [
                label or "",
                str(props.get("data_type") or ""),
                str(props.get("entity_type") or ""),
                str(props.get("catalog_label") or ""),
                str(props.get("usage_kind") or ""),
                str(props.get("usage_type") or ""),
            ]
        ).lower().replace("_", " ")
        compact = value.replace(" ", "")
        if props.get("usage_uuid") or props.get("usage_name") or "usage" in value:
            return "usage"
        if "dataprocessingitem" in compact or "processing item" in value:
            return "processing_item"
        if "dataprocessing" in compact or "process" in value or "job" in value or "traitement" in value:
            return "processing"
        if "control" in value or "quality" in value or "kqi" in value:
            return "control"
        if "field" in value or "column" in value or "attribut" in value:
            return "field"
        if "dataset" in value or "data set" in value:
            return "dataset"
        if "structure" in value or "table" in value:
            return "structure"
        if "source" in value or "database" in value or "filestore" in value:
            return "source"
        return "asset"

    def _category_rank(self, category: str | None) -> int:
        return {
            "field": 0,
            "structure": 1,
            "dataset": 2,
            "processing_item": 3,
            "processing": 4,
            "usage": 5,
            "source": 6,
            "control": 7,
        }.get(str(category or ""), 9)

    def _graph_id(self, node) -> str:
        category = self._category(self._preferred_label(list(node.labels)), dict(node))
        prefix = {
            "processing": "processing",
            "processing_item": "processing-item",
            "structure": "structure",
            "field": "field",
            "usage": "usage",
            "dataset": "dataset",
            "source": "source",
            "control": "control",
        }.get(category, "asset")
        return f"{prefix}:{self._raw_identifier(node)}"

    def _raw_identifier(self, node) -> str:
        props = dict(node)
        return str(props.get("node_id") or props.get("usage_uuid") or node.element_id)

    def _parent_identifier(self, props: dict[str, Any]) -> str | None:
        value = (
            props.get("parent_uuid")
            or props.get("parent_node_id")
            or props.get("parent_id")
            or props.get("parent_usage_uuid")
        )
        return str(value) if value else None

    def _parent_label(self, path: str | None, props: dict[str, Any]) -> str | None:
        explicit = (
            props.get("parent_label")
            or props.get("parent_name")
            or props.get("source_name")
            or props.get("container_name")
            or props.get("structure_name")
        )
        if explicit:
            return str(explicit)
        if not path:
            return None
        parts = [part.strip() for part in re.split(r"[\\/>\|]+", str(path)) if part.strip()]
        if len(parts) >= 2:
            return parts[-2]
        return parts[0] if parts else None
