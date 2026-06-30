from typing import Any

from sqlalchemy import text

from app.db import postgres_conn, neo4j_session


class LinkRepository:
    def find_link_table(self) -> str:
        query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
          AND table_name IN ('link', 'dg_link')
        ORDER BY CASE WHEN table_name = 'link' THEN 1 ELSE 2 END
        """

        with postgres_conn() as conn:
            rows = conn.execute(text(query)).mappings().all()

        if not rows:
            raise RuntimeError("No link table found. Expected 'link' or 'dg_link'.")

        return rows[0]["table_name"]

    def fetch_lineage_links(self, limit: int | None = None) -> list[dict[str, Any]]:
        table = self.find_link_table()

        limit_clause = ""
        params = {}

        if limit is not None:
            limit_clause = "LIMIT :limit"
            params["limit"] = limit

        query = text(f"""
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
            FROM {table}
            WHERE link_type IN ('IsInputOf', 'IsOutputOf', 'IS_INPUT_OF', 'IS_OUTPUT_OF')
              AND src_node_id IS NOT NULL
              AND tgt_node_id IS NOT NULL
            {limit_clause}
        """)

        with postgres_conn() as conn:
            return [dict(row) for row in conn.execute(query, params).mappings().all()]

    def fetch_sample_links(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.fetch_lineage_links(limit=limit)


class SearchRepository:
    TABLES = [
        ("source", "Source", "node_id"),
        ("container", "Container", "node_id"),
        ("structure", "Structure", "node_id"),
        ("field", "Field", "node_id"),
        ("usage", "Usage", "usage_uuid"),
    ]

    def existing_tables(self) -> set[str]:
        query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        """

        with postgres_conn() as conn:
            rows = conn.execute(text(query)).mappings().all()

        return {row["table_name"] for row in rows}

    def search_assets(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        existing = self.existing_tables()
        results: list[dict[str, Any]] = []
        exact_query = q.strip()

        for table_name, asset_type, pk in self.TABLES:
            if table_name not in existing:
                continue

            columns_query = text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = :table_name
            """)

            with postgres_conn() as conn:
                cols = {
                    row["column_name"]
                    for row in conn.execute(
                        columns_query,
                        {"table_name": table_name},
                    ).mappings().all()
                }

            if pk in cols:
                select_name = (
                    "name_label"
                    if "name_label" in cols
                    else "usage_name"
                    if "usage_name" in cols
                    else "NULL"
                )

                select_tech = (
                    "name_tech"
                    if "name_tech" in cols
                    else "usage_tech_name"
                    if "usage_tech_name" in cols
                    else "NULL"
                )

                select_path = (
                    "path_full"
                    if "path_full" in cols
                    else "usage_path"
                    if "usage_path" in cols
                    else "NULL"
                )

                exact_sql = text(f"""
                    SELECT
                        CAST({pk} AS TEXT) AS node_id,
                        CAST({select_name} AS TEXT) AS name,
                        CAST({select_tech} AS TEXT) AS technical_name,
                        CAST({select_path} AS TEXT) AS path
                    FROM {table_name}
                    WHERE CAST({pk} AS TEXT) = :node_id
                    LIMIT :limit
                """)

                with postgres_conn() as conn:
                    exact_rows = conn.execute(
                        exact_sql,
                        {"node_id": exact_query, "limit": limit},
                    ).mappings().all()

                for row in exact_rows:
                    node_id = row["node_id"]
                    results.append(
                        {
                            "id": f"{asset_type.lower()}:{node_id}",
                            "node_id": node_id,
                            "type": asset_type,
                            "name": row["name"],
                            "technical_name": row["technical_name"],
                            "path": row["path"],
                            "source": "postgres",
                        }
                    )

                if exact_rows:
                    return results[:limit]

                if len(results) >= limit:
                    break

            searchable_cols = [
                c for c in ["name_label", "name_tech", "path_full", "usage_name", "usage_tech_name", "usage_path"]
                if c in cols
            ]

            if not searchable_cols:
                continue

            where_clause = " OR ".join(
                [f"LOWER(CAST({c} AS TEXT)) LIKE LOWER(:pattern)" for c in searchable_cols]
            )

            select_name = (
                "name_label"
                if "name_label" in cols
                else "usage_name"
                if "usage_name" in cols
                else "NULL"
            )

            select_tech = (
                "name_tech"
                if "name_tech" in cols
                else "usage_tech_name"
                if "usage_tech_name" in cols
                else "NULL"
            )

            select_path = (
                "path_full"
                if "path_full" in cols
                else "usage_path"
                if "usage_path" in cols
                else "NULL"
            )

            query = text(f"""
                SELECT
                    CAST({pk} AS TEXT) AS node_id,
                    CAST({select_name} AS TEXT) AS name,
                    CAST({select_tech} AS TEXT) AS technical_name,
                    CAST({select_path} AS TEXT) AS path
                FROM {table_name}
                WHERE {where_clause}
                LIMIT :limit
            """)

            with postgres_conn() as conn:
                rows = conn.execute(
                    query,
                    {
                        "pattern": f"%{q}%",
                        "limit": limit,
                    },
                ).mappings().all()

            for row in rows:
                node_id = row["node_id"]
                results.append(
                    {
                        "id": f"{asset_type.lower()}:{node_id}",
                        "node_id": node_id,
                        "type": asset_type,
                        "name": row["name"],
                        "technical_name": row["technical_name"],
                        "path": row["path"],
                        "source": "postgres",
                    }
                )

            if len(results) >= limit:
                break

        return results[:limit]


class Neo4jRepository:
    CORE_LINEAGE_RELATIONSHIP_TYPES = [
        "CONTAINS",
        "HAS_FIELD",
        "IS_INPUT_OF",
        "IS_OUTPUT_OF",
        "PART_OF",
        "FLOWS_TO",
    ]

    LINEAGE_RELATIONSHIP_TYPES = [
        *CORE_LINEAGE_RELATIONSHIP_TYPES,
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
    ]

    USAGE_RELATIONSHIP_TYPES = [
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
    ]

    USAGE_PARENT_RELATIONSHIP_TYPES = [
        "GENERALIZES",
        "SPECIALIZES",
        "REGROUPS",
        "IS_PART_OF_DIMENSION",
        "HAS_FOR_UNIVERSE",
        "IS_UNIVERSE_OF",
        "IS_LINKED_TO",
        "HAS_FOR_SOURCE",
        "IS_SOURCE_OF",
        "IS_USAGE_SOURCE_FOR",
        "IS_USAGE_DESTINATION_FOR",
    ]

    USAGE_SOURCE_RELATIONSHIP_TYPES = [
        "USES",
        "IS_USED_BY",
        "HAS_FOR_SOURCE",
        "IS_SOURCE_OF",
        "IS_LINKED_TO",
        "IS_USAGE_SOURCE_FOR",
        "IS_USAGE_DESTINATION_FOR",
    ]

    LABEL_PRIORITY = [
        "DataProcessingItem",
        "DataProcessing",
        "Usage",
        "BusinessTerm",
        "Field",
        "Structure",
        "Container",
        "Source",
    ]

    KIND_BY_LABEL = {
        "source": "source",
        "container": "container",
        "structure": "structure",
        "field": "field",
        "dataprocessing": "data_processing",
        "dataprocessingitem": "data_processing_item",
        "usage": "usage",
        "businessterm": "business_term",
    }

    def _preferred_label(self, labels: list[str]) -> str:
        normalized = {label.lower(): label for label in labels}
        for preferred in self.LABEL_PRIORITY:
            if preferred.lower() in normalized:
                return normalized[preferred.lower()]
        return labels[0] if labels else "Node"

    def _node_kind(self, labels: list[str], props: dict[str, Any]) -> str:
        label_values = {label.lower().replace("_", "") for label in labels}
        prop_values = {
            str(props.get("data_type") or "").lower().replace("_", ""),
            str(props.get("entity_type") or "").lower().replace("_", ""),
            str(props.get("catalog_label") or "").lower().replace("_", ""),
        }
        if props.get("usage_uuid") or props.get("usage_name"):
            return "usage"
        for key, kind in self.KIND_BY_LABEL.items():
            if key in label_values or key in prop_values:
                return kind
        return "unknown"

    def get_business_subgraph(self, node_id: str, depth: int = 2) -> dict[str, Any]:
        depth = max(1, min(depth, 10))
        usage_record = self._fetch_usage_quality_subgraph_record(node_id=node_id)
        if usage_record is not None:
            return self._attach_dqc_quality_checks(self._format_subgraph_record(usage_record))

        record = self._fetch_business_subgraph_record(
            root_match="MATCH (root:DataGalaxyObject) WHERE root.node_id = $node_id OR root.usage_uuid = $node_id",
            node_id=node_id,
            depth=depth,
        )

        if record is None:
            record = self._fetch_business_subgraph_record(
                root_match="MATCH (root) WHERE root.node_id = $node_id OR root.usage_uuid = $node_id",
                node_id=node_id,
                depth=depth,
            )

        if record is None:
            return {"nodes": [], "edges": []}

        return self._attach_dqc_quality_checks(self._format_subgraph_record(record))

    def _fetch_usage_quality_subgraph_record(self, node_id: str):
        query = """
        MATCH (root:Usage)
        WHERE root.usage_uuid = $node_id OR root.node_id = $node_id
        OPTIONAL MATCH (root)-[usage_rel]-(usage_neighbor:Usage)
        WHERE type(usage_rel) IN $usage_rel_types
        WITH root,
             collect(DISTINCT usage_neighbor) AS usage_neighbors,
             collect(DISTINCT usage_rel) AS usage_rels
        OPTIONAL MATCH (root)-[source_rel]-(source:Source)
        WHERE type(source_rel) IN $usage_source_rel_types
        WITH root,
             usage_neighbors,
             usage_rels,
             collect(DISTINCT source) AS sources,
             collect(DISTINCT source_rel) AS source_rels
        WITH root,
             usage_neighbors,
             usage_rels,
             sources,
             source_rels,
             coalesce(root.parent_uuid, root.parent_node_id) AS parent_id
        OPTIONAL MATCH (property_parent:Usage)
        WHERE parent_id IS NOT NULL
          AND (property_parent.node_id = parent_id OR property_parent.usage_uuid = parent_id)
        WITH root,
             usage_neighbors,
             usage_rels,
             sources,
             source_rels,
             collect(DISTINCT property_parent) AS property_parent_nodes
        WITH [root] + usage_neighbors + sources + property_parent_nodes AS all_nodes,
             usage_rels + source_rels AS all_rels,
             root
        UNWIND all_nodes AS n
        WITH root, all_rels, collect(DISTINCT n) AS nodes
        UNWIND all_rels AS r
        WITH root, nodes, r WHERE r IS NOT NULL
        WITH root, nodes, collect(DISTINCT r) AS rels
        RETURN root, nodes, rels
        """
        with neo4j_session() as session:
            return session.run(
                query,
                node_id=node_id,
                usage_rel_types=self.USAGE_PARENT_RELATIONSHIP_TYPES,
                usage_source_rel_types=self.USAGE_SOURCE_RELATIONSHIP_TYPES,
            ).single()

    def get_usage_neighbors(self, node_id: str) -> dict[str, Any]:
        query = """
        MATCH (start)
        WHERE start.node_id = $node_id OR start.usage_uuid = $node_id
        OPTIONAL MATCH (start)-[r]-(usage:Usage)
        WHERE type(r) IN $usage_rel_types
        RETURN start, collect({rel: r, usage: usage}) AS usage_neighbors
        """
        with neo4j_session() as session:
            record = session.run(
                query,
                node_id=node_id,
                usage_rel_types=self.USAGE_RELATIONSHIP_TYPES,
            ).single()
        if record is None or record["start"] is None:
            return {"start_node": None, "usage_neighbors": []}

        start = self._format_node(record["start"])
        neighbors = []
        for item in record["usage_neighbors"] or []:
            rel = item.get("rel")
            usage = item.get("usage")
            if rel is None or usage is None:
                continue
            source_id = rel.start_node.get("node_id") or rel.start_node.get("usage_uuid") or str(rel.start_node.element_id)
            direction = "outgoing" if source_id == start["id"] else "incoming"
            neighbors.append(
                {
                    "usage_node": self._format_node(usage),
                    "relationship_type": rel.type,
                    "direction": direction,
                    "relationship": {
                        "id": str(rel.element_id),
                        "type": rel.type,
                        "properties": dict(rel),
                    },
                }
            )
        return {"start_node": start, "usage_neighbors": neighbors}

    def _fetch_business_subgraph_record(self, root_match: str, node_id: str, depth: int):
        core_rel_pattern = ":" + "|".join(self.CORE_LINEAGE_RELATIONSHIP_TYPES)
        query = f"""
        {root_match}
        CALL {{
            WITH root
            OPTIONAL MATCH direct_usage_path = (root)-[direct_usage_rel]-(direct_usage_neighbor)
            WHERE type(direct_usage_rel) IN $usage_rel_types
              AND (root:Usage OR direct_usage_neighbor:Usage)
            WITH direct_usage_path, direct_usage_neighbor
            WHERE direct_usage_path IS NOT NULL
            LIMIT $max_direct_usage_paths
            RETURN
                collect(DISTINCT direct_usage_path) AS direct_usage_paths,
                collect(DISTINCT direct_usage_neighbor) AS direct_usage_neighbors
        }}
        CALL {{
            WITH root
            OPTIONAL MATCH usage_parent_path = (root)-[usage_parent_rel]-(usage_parent)
            WHERE type(usage_parent_rel) IN $usage_parent_rel_types
              AND (root:Usage OR usage_parent:Usage)
            WITH usage_parent_path, usage_parent
            WHERE usage_parent_path IS NOT NULL
            LIMIT $max_usage_parent_paths
            RETURN
                collect(DISTINCT usage_parent_path) AS usage_parent_paths,
                collect(DISTINCT usage_parent) AS usage_parent_nodes
        }}
        CALL {{
            WITH root
            WITH root, coalesce(root.parent_uuid, root.parent_node_id, root.parent_id, root.parent_usage_uuid) AS parent_id
            OPTIONAL MATCH (property_parent)
            WHERE parent_id IS NOT NULL
              AND (property_parent.node_id = parent_id OR property_parent.usage_uuid = parent_id)
            RETURN collect(DISTINCT property_parent) AS property_parent_nodes
        }}
        WITH root,
             direct_usage_paths,
             usage_parent_paths,
             [root] + direct_usage_neighbors + usage_parent_nodes + property_parent_nodes AS seed_nodes,
             CASE
                WHEN root:Usage
                THEN [root] + usage_parent_nodes + property_parent_nodes + [
                    direct_neighbor IN direct_usage_neighbors
                    WHERE NOT direct_neighbor:Source AND NOT direct_neighbor:Container
                ]
                ELSE [root] + direct_usage_neighbors + usage_parent_nodes + property_parent_nodes
             END AS core_seed_nodes
        CALL {{
            WITH core_seed_nodes
            UNWIND core_seed_nodes AS seed
            WITH DISTINCT seed WHERE seed IS NOT NULL
            OPTIONAL MATCH core_path = (seed)-[{core_rel_pattern}*1..{depth}]-(neighbor)
            WITH core_path
            WHERE core_path IS NOT NULL
            LIMIT $max_paths
            RETURN collect(core_path) AS core_paths
        }}
        CALL {{
            WITH root, seed_nodes, core_paths
            WITH [root] + seed_nodes + reduce(nodes_acc = [], p IN core_paths | nodes_acc + nodes(p)) AS candidate_nodes
            UNWIND candidate_nodes AS candidate
            WITH DISTINCT candidate WHERE candidate IS NOT NULL
            OPTIONAL MATCH usage_path = (candidate)-[usage_rel]-(usage:Usage)
            WHERE type(usage_rel) IN $usage_rel_types
            WITH usage_path
            WHERE usage_path IS NOT NULL
            LIMIT $max_usage_paths
            RETURN collect(usage_path) AS usage_paths
        }}
        WITH root, direct_usage_paths + usage_parent_paths + core_paths + usage_paths AS paths, seed_nodes
        CALL {{
            WITH root, paths, seed_nodes
            WITH [root] + seed_nodes + reduce(nodes_acc = [], p IN paths | CASE WHEN p IS NULL THEN nodes_acc ELSE nodes_acc + nodes(p) END) AS all_nodes
            UNWIND all_nodes AS n
            RETURN collect(DISTINCT n) AS nodes
        }}
        CALL {{
            WITH paths
            UNWIND paths AS p
            WITH p WHERE p IS NOT NULL
            UNWIND relationships(p) AS r
            RETURN collect(DISTINCT r) AS rels
        }}
        RETURN root, nodes, rels
        """

        with neo4j_session() as session:
            record = session.run(
                query,
                node_id=node_id,
                usage_rel_types=self.USAGE_RELATIONSHIP_TYPES,
                usage_parent_rel_types=self.USAGE_PARENT_RELATIONSHIP_TYPES,
                max_paths=650,
                max_usage_paths=350,
                max_direct_usage_paths=40,
                max_usage_parent_paths=60,
            ).single()

        return record

    def _format_subgraph_record(self, record) -> dict[str, Any]:
        nodes = []
        edges = []
        nodes_by_id = {}
        edge_pairs = set()

        for n in record["nodes"]:
            formatted = self._format_node(n)
            nodes.append(formatted)
            nodes_by_id[formatted["id"]] = formatted

        for r in record["rels"]:
            props = dict(r)
            source_id = r.start_node.get("node_id") or r.start_node.get("usage_uuid") or str(r.start_node.element_id)
            target_id = r.end_node.get("node_id") or r.end_node.get("usage_uuid") or str(r.end_node.element_id)
            flow_source_id = source_id
            flow_target_id = target_id
            flow_type = r.type
            flow_direction = "forward"

            # DataGalaxy stores "field IsOutputOf process" even though the data
            # flow is "process -> output field". Keep storage endpoints in
            # properties and orient the API edge for left-to-right lineage.
            if r.type == "IS_OUTPUT_OF":
                flow_source_id = target_id
                flow_target_id = source_id
                flow_direction = "reversed_for_data_flow"
            elif self._is_source_usage_relationship(r):
                source_endpoint = self._relationship_endpoint_by_kind(r, "source")
                usage_endpoint = self._relationship_endpoint_by_kind(r, "usage")
                if source_endpoint and usage_endpoint:
                    flow_source_id = source_endpoint
                    flow_target_id = usage_endpoint
                    flow_type = "SOURCE_USES_USAGE"
                    flow_direction = "source_to_usage"
            elif self._is_usage_usage_relationship(r):
                flow_type = "USAGE_DEPENDS_ON"

            edges.append(
                {
                    "id": str(r.element_id),
                    "source": flow_source_id,
                    "target": flow_target_id,
                    "type": flow_type,
                    "properties": {
                        **props,
                        "raw_type": r.type,
                        "storage_source": source_id,
                        "storage_target": target_id,
                        "flow_direction": flow_direction,
                    },
                }
            )
            edge_pairs.add((flow_source_id, flow_target_id))
            edge_pairs.add((flow_target_id, flow_source_id))

        root = record.get("root") if "root" in record.keys() else None
        if root is not None:
            root_id = root.get("node_id") or root.get("usage_uuid") or str(root.element_id)
            root_props = dict(root)
            parent_id = (
                root_props.get("parent_uuid")
                or root_props.get("parent_node_id")
                or root_props.get("parent_id")
                or root_props.get("parent_usage_uuid")
            )
            if parent_id and parent_id in nodes_by_id and root_id in nodes_by_id and (parent_id, root_id) not in edge_pairs:
                edges.append(
                    {
                        "id": f"synthetic:usage-parent:{parent_id}->{root_id}",
                        "source": parent_id,
                        "target": root_id,
                        "type": "USAGE_DEPENDS_ON",
                        "properties": {
                            "storage_source": parent_id,
                            "storage_target": root_id,
                            "flow_direction": "synthetic_parent_link",
                            "synthetic": True,
                            "raw_type": "USAGE_PARENT",
                        },
                    }
                )

        return {"nodes": nodes, "edges": edges}

    def _relationship_endpoint_by_kind(self, r, kind: str) -> str | None:
        for endpoint in (r.start_node, r.end_node):
            labels = list(endpoint.labels)
            props = dict(endpoint)
            if self._node_kind(labels, props) == kind:
                return props.get("node_id") or props.get("usage_uuid") or str(endpoint.element_id)
        return None

    def _is_source_usage_relationship(self, r) -> bool:
        kinds = {
            self._node_kind(list(r.start_node.labels), dict(r.start_node)),
            self._node_kind(list(r.end_node.labels), dict(r.end_node)),
        }
        return kinds == {"source", "usage"} and r.type in self.USAGE_SOURCE_RELATIONSHIP_TYPES

    def _is_usage_usage_relationship(self, r) -> bool:
        kinds = [
            self._node_kind(list(r.start_node.labels), dict(r.start_node)),
            self._node_kind(list(r.end_node.labels), dict(r.end_node)),
        ]
        return kinds == ["usage", "usage"] and r.type in self.USAGE_RELATIONSHIP_TYPES

    def _format_node(self, n) -> dict[str, Any]:
        props = dict(n)
        labels = list(n.labels)
        node_identifier = props.get("node_id") or props.get("usage_uuid") or str(n.element_id)
        label = (
            props.get("usage_name")
            or props.get("name_label")
            or props.get("name")
            or props.get("name_tech")
            or props.get("usage_tech_name")
            or node_identifier
        )
        usage_metadata = {
            "usage_uuid": props.get("usage_uuid"),
            "usage_name": props.get("usage_name") or props.get("name_label") or props.get("name"),
            "usage_tech_name": props.get("usage_tech_name") or props.get("name_tech"),
            "usage_path": props.get("usage_path") or props.get("path_full") or props.get("path"),
            "usage_kind": props.get("usage_kind") or props.get("usage_type"),
            "app_code": props.get("app_code") or props.get("application_code"),
            "status": props.get("status"),
            "parent_uuid": props.get("parent_uuid"),
            "parent_type": props.get("parent_type"),
            "parent_data_type": props.get("parent_data_type"),
        }
        quality = self._usage_quality(props) if self._node_kind(labels, props) == "usage" else None
        quality_checks = self._usage_table_quality_checks(props) if quality else []
        clean_props = {**props, **{k: v for k, v in usage_metadata.items() if v is not None}, "labels": labels}
        if quality:
            clean_props["quality"] = quality
            clean_props["quality_checks"] = quality_checks
        return {
            "id": node_identifier,
            "node_id": props.get("node_id"),
            "label": label,
            "type": self._preferred_label(labels),
            "kind": self._node_kind(labels, props),
            "quality": quality,
            "quality_checks": quality_checks,
            "properties": clean_props,
        }

    def _usage_quality(self, props: dict[str, Any]) -> dict[str, Any]:
        return {
            "status_score": props.get("status_score"),
            "usage_quality_score": props.get("usage_quality_score"),
            "usage_quality_status": props.get("usage_quality_status") or props.get("status"),
            "source_quality_score": props.get("source_quality_score"),
            "source_quality_status": props.get("source_quality_status"),
        }

    def _usage_table_quality_checks(self, props: dict[str, Any]) -> list[dict[str, Any]]:
        checks = []
        if props.get("usage_quality_score") is not None or props.get("usage_quality_status") is not None:
            checks.append(
                {
                    "control_source": "USAGE_TABLE",
                    "control_name": "Usage quality score",
                    "field": "usage_quality_score",
                    "score": props.get("usage_quality_score"),
                    "status": props.get("usage_quality_status") or props.get("status"),
                }
            )
        if props.get("status_score") is not None:
            checks.append(
                {
                    "control_source": "USAGE_TABLE",
                    "control_name": "Status score",
                    "field": "status_score",
                    "score": props.get("status_score"),
                    "status": props.get("usage_quality_status") or props.get("status"),
                }
            )
        if props.get("source_quality_score") is not None or props.get("source_quality_status") is not None:
            checks.append(
                {
                    "control_source": "USAGE_TABLE",
                    "control_name": "Source quality score",
                    "field": "source_quality_score",
                    "score": props.get("source_quality_score"),
                    "status": props.get("source_quality_status"),
                }
            )
        return checks

    def _attach_dqc_quality_checks(self, graph: dict[str, Any]) -> dict[str, Any]:
        nodes = graph.setdefault("nodes", [])
        edges = graph.setdefault("edges", [])
        node_by_id = {node["id"]: node for node in nodes}
        dqc_rows = self._fetch_dqc_checks_for_graph_nodes(nodes)
        missing_field_ids = sorted(
            {
                row["matched_node_id"]
                for row in dqc_rows
                if row.get("matched_node_id")
                and row.get("matched_node_id") not in node_by_id
                and self._is_dqc_field_control(row)
            }
        )

        for field_node in self._fetch_nodes_by_ids(missing_field_ids):
            if field_node["id"] in node_by_id:
                continue
            nodes.append(field_node)
            node_by_id[field_node["id"]] = field_node

        edge_ids = {edge.get("id") for edge in edges}
        for row in dqc_rows:
            check = self._format_dqc_check(row)
            target_id = row.get("matched_node_id")
            target = node_by_id.get(target_id)
            source = self._find_source_for_dqc_row(row, nodes)

            if target and target.get("kind") == "field":
                self._append_quality_check(target, check, quality_kind="field")
                if source:
                    self._append_quality_check(source, check, quality_kind="source")
                    self._link_source_to_controlled_field(source, target, edges, edge_ids)
                continue

            if target and target.get("kind") == "usage":
                self._append_quality_check(target, check, quality_kind="usage")
                continue

            if target and target.get("kind") == "source":
                self._append_quality_check(target, check, quality_kind="source")
                continue

            if self._is_dqc_field_control(row) and source and target:
                self._append_quality_check(target, check, quality_kind="field")
                self._append_quality_check(source, check, quality_kind="source")
                self._link_source_to_controlled_field(source, target, edges, edge_ids)
                continue

            if source:
                self._append_quality_check(source, check, quality_kind="source")

        return graph

    def _fetch_dqc_checks_for_graph_nodes(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        node_ids = [node["id"] for node in nodes if node.get("id")]
        usage_app_codes = sorted(
            {
                str(node.get("properties", {}).get("app_code") or "").strip().upper()
                for node in nodes
                if node.get("kind") == "usage" and node.get("properties", {}).get("app_code")
            }
        )
        source_names = sorted(
            {
                str(value).strip().lower()
                for node in nodes
                if node.get("kind") == "source"
                for value in [
                    node.get("label"),
                    node.get("properties", {}).get("name_label"),
                    node.get("properties", {}).get("name_tech"),
                    node.get("properties", {}).get("path_full"),
                ]
                if value
            }
        )
        if not node_ids and not usage_app_codes and not source_names:
            return []
        query = text("""
            SELECT r.id AS check_id,
                   r.matched_node_id,
                   r.matched_entity_level,
                   r.matched_path_full,
                   r.match_method,
                   r.match_score,
                   r.confidence_level,
                   r.human_review_required,
                   r.resolution_status,
                   n.application_code_norm,
                   n.application_code_raw,
                   n.controlled_object_name_raw,
                   n.controlled_source_name_raw,
                   n.controlled_structure_name,
                   n.controlled_field_name,
                   n.target_level,
                   n.quality_dimension,
                   n.control_name,
                   n.control_tool,
                   n.control_link,
                   n.acceptance_threshold,
                   n.controlled_item_count,
                   n.ok_count,
                   n.ko_count,
                   n.ko_rate,
                   n.quality_score
            FROM dqc_resolved r
            JOIN dqc_normalized n ON n.id = r.normalized_id
            WHERE r.matched_node_id = ANY(:node_ids)
               OR UPPER(COALESCE(n.application_code_norm, '')) = ANY(:app_codes)
               OR LOWER(COALESCE(n.controlled_source_name_raw, '')) = ANY(:source_names)
            ORDER BY r.id DESC
            LIMIT 200
        """)
        try:
            with postgres_conn() as conn:
                rows = conn.execute(
                    query,
                    {
                        "node_ids": node_ids,
                        "app_codes": usage_app_codes,
                        "source_names": source_names,
                    },
                ).mappings().all()
        except Exception:
            return []

        return [dict(row) for row in rows]

    def _fetch_nodes_by_ids(self, node_ids: list[str]) -> list[dict[str, Any]]:
        if not node_ids:
            return []
        query = """
        MATCH (n:Field)
        WHERE n.node_id IN $node_ids
        RETURN collect(DISTINCT n) AS nodes
        """
        with neo4j_session() as session:
            record = session.run(query, node_ids=node_ids).single()
        if record is None:
            return []
        return [self._format_node(node) for node in record["nodes"]]

    def _format_dqc_check(self, row: dict[str, Any]) -> dict[str, Any]:
        metrics = self._dqc_control_metrics(row)
        object_type = row.get("matched_entity_level") or row.get("target_level") or "DQC"
        object_name = (
            row.get("controlled_field_name")
            or row.get("controlled_structure_name")
            or row.get("controlled_object_name_raw")
            or row.get("matched_path_full")
        )
        return {
            "control_source": "DQC",
            "check_id": row.get("check_id"),
            "control_name": row.get("control_name") or row.get("quality_dimension") or "Controlled item quality check",
            "controlled_object_type": str(object_type or "").upper(),
            "controlled_object_name": object_name,
            "score": metrics["control_score"],
            "status": metrics["control_status"],
            "control_ratio": metrics["control_ratio"],
            "control_score": metrics["control_score"],
            "control_status": metrics["control_status"],
            "field": row.get("controlled_field_name"),
            **row,
        }

    def _dqc_control_metrics(self, row: dict[str, Any]) -> dict[str, Any]:
        controlled = row.get("controlled_item_count")
        ok = row.get("ok_count")
        threshold = row.get("acceptance_threshold")
        ratio = None
        score = row.get("quality_score")
        status = "UNKNOWN"

        if controlled:
            ratio = ok / controlled if ok is not None else None
            score = round(ratio * 100, 2) if ratio is not None else score

        if score is not None and threshold is not None:
            threshold_score = threshold * 100 if threshold <= 1 else threshold
            status = "PASSED" if score >= threshold_score else "FAILED"
        elif score is not None:
            status = "NO_THRESHOLD"

        return {
            "control_ratio": round(ratio, 6) if ratio is not None else None,
            "control_score": score,
            "control_status": status,
        }

    def _append_quality_check(self, node: dict[str, Any], check: dict[str, Any], quality_kind: str) -> None:
        props = node.setdefault("properties", {})
        checks = list(node.get("quality_checks") or props.get("quality_checks") or [])
        key = check.get("check_id") or f"{check.get('control_source')}:{check.get('field')}:{check.get('control_name')}"
        if not any((item.get("check_id") or f"{item.get('control_source')}:{item.get('field')}:{item.get('control_name')}") == key for item in checks):
            checks.append(check)
        node["quality_checks"] = checks
        props["quality_checks"] = checks
        quality = self._quality_from_checks(checks, quality_kind)
        existing_quality = node.get("quality") or props.get("quality") or {}
        merged_quality = {**existing_quality, **quality}
        node["quality"] = merged_quality
        props["quality"] = merged_quality

    def _quality_from_checks(self, checks: list[dict[str, Any]], quality_kind: str) -> dict[str, Any]:
        scores = [
            float(score)
            for score in [check.get("score") or check.get("quality_score") for check in checks]
            if score is not None
        ]
        score = round(sum(scores) / len(scores), 2) if scores else None
        status = "FAILED" if any(
            str(check.get("control_status") or check.get("status") or "").upper() in {"KO", "FAILED", "CRITICAL"}
            for check in checks
        ) else "PASSED"
        if quality_kind == "field":
            return {"field_quality_score": score, "field_quality_status": status, "score": score, "status": status}
        if quality_kind == "source":
            return {"source_quality_score": score, "source_quality_status": status, "score": score, "status": status}
        return {"score": score, "status": status}

    def _is_dqc_field_control(self, row: dict[str, Any]) -> bool:
        level = str(row.get("matched_entity_level") or row.get("target_level") or "").lower()
        return level == "field" or bool(row.get("controlled_field_name"))

    def _find_source_for_dqc_row(self, row: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
        sources = [node for node in nodes if node.get("kind") == "source"]
        if not sources:
            return None
        source_hint = self._norm(row.get("controlled_source_name_raw") or row.get("controlled_source_name_norm"))
        app_hint = self._norm(row.get("application_code_norm") or row.get("application_code_raw"))
        matched_path = self._norm(row.get("matched_path_full"))
        for source in sources:
            props = source.get("properties", {})
            source_values = [
                source.get("id"),
                source.get("label"),
                props.get("node_id"),
                props.get("name_label"),
                props.get("name_tech"),
                props.get("path_full"),
                props.get("path"),
            ]
            source_text = " ".join(self._norm(value) for value in source_values if value)
            source_app = self._norm(props.get("app_code") or props.get("application_code"))
            if source_hint and source_hint in source_text:
                return source
            if matched_path and any(self._norm(value) and self._norm(value) in matched_path for value in source_values):
                return source
            if not source_hint and app_hint and source_app and app_hint == source_app:
                return source
        return None

    def _link_source_to_controlled_field(
        self,
        source: dict[str, Any],
        field: dict[str, Any],
        edges: list[dict[str, Any]],
        edge_ids: set[str],
    ) -> None:
        source_id = source["id"]
        field_id = field["id"]
        edge_id = f"synthetic:dqc-source-field:{source_id}->{field_id}"
        if edge_id in edge_ids:
            return
        field.setdefault("properties", {})["parent_source_id"] = source_id
        field["parent_source_id"] = source_id
        edges.append(
            {
                "id": edge_id,
                "source": source_id,
                "target": field_id,
                "type": "HAS_FIELD",
                "properties": {
                    "synthetic": True,
                    "flow_direction": "source_to_controlled_field",
                    "raw_type": "DQC_CONTROLLED_FIELD",
                },
            }
        )
        edge_ids.add(edge_id)

    def _norm(self, value: Any) -> str:
        return str(value or "").strip().lower()
