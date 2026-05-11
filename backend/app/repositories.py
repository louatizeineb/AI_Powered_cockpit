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
    def get_business_subgraph(self, node_id: str, depth: int = 2) -> dict[str, Any]:
        depth = max(1, min(depth, 5))

        query = f"""
        MATCH (root {{node_id: $node_id}})
        OPTIONAL MATCH path = (root)-[*1..{depth}]-(neighbor)
        WITH root, collect(path) AS paths
        CALL {{
            WITH root, paths
            WITH [root] + reduce(nodes_acc = [], p IN paths | nodes_acc + nodes(p)) AS all_nodes
            UNWIND all_nodes AS n
            RETURN collect(DISTINCT n) AS nodes
        }}
        CALL {{
            WITH paths
            UNWIND paths AS p
            UNWIND relationships(p) AS r
            RETURN collect(DISTINCT r) AS rels
        }}
        RETURN nodes, rels
        """

        with neo4j_session() as session:
            record = session.run(query, node_id=node_id).single()

        if record is None:
            return {"nodes": [], "edges": []}

        nodes = []
        edges = []

        for n in record["nodes"]:
            props = dict(n)
            labels = list(n.labels)
            node_identifier = props.get("node_id") or props.get("usage_uuid") or str(n.element_id)
            label = props.get("name_label") or props.get("name") or props.get("name_tech") or node_identifier

            nodes.append(
                {
                    "id": node_identifier,
                    "node_id": props.get("node_id"),
                    "label": label,
                    "type": labels[0] if labels else "Node",
                    "properties": props,
                }
            )

        for r in record["rels"]:
            props = dict(r)
            source_id = r.start_node.get("node_id") or r.start_node.get("usage_uuid") or str(r.start_node.element_id)
            target_id = r.end_node.get("node_id") or r.end_node.get("usage_uuid") or str(r.end_node.element_id)

            edges.append(
                {
                    "id": str(r.element_id),
                    "source": source_id,
                    "target": target_id,
                    "type": r.type,
                    "properties": props,
                }
            )

        return {"nodes": nodes, "edges": edges}