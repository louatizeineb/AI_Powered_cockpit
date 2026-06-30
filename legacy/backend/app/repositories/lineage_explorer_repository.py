from __future__ import annotations

import re
from typing import Any

from app.db import neo4j_session


class LineageExplorerRepository:
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
        needle = q.strip().lower()
        looks_like_path = any(separator in needle for separator in ["\\", "/", ">", "|"])
        fetch_limit = min(max(limit * (2 if looks_like_path else 5), 20), 60 if looks_like_path else 150)
        path_query = """
        CALL {
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
        }
        WITH n, min(rank) AS rank
        ORDER BY rank,
             coalesce(n.path_full, n.usage_path, n.name_label, n.usage_name, n.name_tech, n.name)
        RETURN collect(n)[0..$limit] AS nodes
        """
        exact_query = """
        CALL {
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
        }
        WITH n, min(rank) AS rank
        ORDER BY rank,
             CASE
                WHEN n:Field THEN 0
                WHEN n:Structure THEN 1
                WHEN n:DataProcessingItem THEN 2
                WHEN n:DataProcessing THEN 3
                WHEN n:Usage THEN 4
                ELSE 5
             END,
             coalesce(n.path_full, n.usage_path, n.name_label, n.usage_name, n.name_tech, n.name)
        RETURN collect(n)[0..$limit] AS nodes
        """
        partial_query = """
        CALL {
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
                ELSE 5
             END,
             coalesce(n.path_full, n.usage_path, n.name_label, n.usage_name, n.name_tech, n.name)
        RETURN collect(n)[0..$limit] AS nodes
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

        nodes = [self._format_node(node) for node in exact_nodes[:fetch_limit]]
        flagged = self._attach_direction_flags(nodes)
        flagged.sort(
            key=lambda node: (
                not (node.get("has_upstream") or node.get("has_downstream")),
                self._category_rank(node.get("category")),
                str(node.get("path") or node.get("label") or ""),
            )
        )
        return flagged[:limit]

    def get_neighbors(self, node_id: str, direction: str, limit: int = 50) -> dict[str, Any] | None:
        query = """
        CALL {
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
        CALL {
            WITH center
            MATCH (center)-[r]-(neighbor)
            WHERE type(r) IN $relationship_types
            RETURN collect(DISTINCT {rel: r, neighbor: neighbor}) AS items
        }
        CALL {
            WITH items
            UNWIND items AS item
            WITH item.neighbor AS neighbor
            OPTIONAL MATCH (neighbor)-[context_rel]-(context)
            WHERE type(context_rel) IN $context_relationship_types
              AND (
                (neighbor:DataProcessingItem AND context:DataProcessing)
                OR (neighbor:Field AND (context:Structure OR context:Table OR context:Dataset))
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
                context_relationship_types=["PART_OF", "PartOf"],
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
            if rel is None or context is None or not self._should_include_relationship(rel):
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

        flagged = self._attach_direction_flags([formatted_center, *node_by_id.values()])
        flagged_by_id = {node["id"]: node for node in flagged}
        return {
            "center": flagged_by_id.get(formatted_center["id"], formatted_center),
            "nodes": [flagged_by_id.get(node["id"], node) for node in node_by_id.values()],
            "edges": list(edge_by_id.values()),
        }

    def _attach_direction_flags(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not nodes:
            return []

        identifiers = sorted({node["node_id"] for node in nodes if node.get("node_id")})
        query = """
        CALL {
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
        CALL {
            WITH n
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

        return [{**node, **flags.get(node["node_id"], {})} for node in nodes]

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
        clean_props = {**props, "labels": labels}
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
        elif rel_type == "PART_OF":
            source, target = start, end
            display_type = "PROCESSING_CONTEXT"

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
                **dict(rel),
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
        if rel_type not in self.MEANINGFUL_RELATIONSHIP_TYPES:
            return False
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
        }
        return mapping.get(compact, str(value or "").upper())

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
        if category == "source":
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
