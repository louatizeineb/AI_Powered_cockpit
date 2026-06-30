from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


def _prepare_lookup_tables(conn, export_id: str) -> None:
    statements = [
        "DROP TABLE IF EXISTS migration_v2_usage_ref_tmp",
        "DROP TABLE IF EXISTS migration_v2_source_app_tmp",
        "DROP TABLE IF EXISTS migration_v2_structure_name_tmp",
        """
        CREATE TEMP TABLE migration_v2_usage_ref_tmp ON COMMIT DROP AS
        SELECT
            node_id,
            status,
            NULLIF(btrim(app_code), '') AS app_code,
            lower(NULLIF(btrim(app_code), '')) AS norm_app_code,
            NULLIF(btrim(coalesce(raw_payload->>'v_dataset', raw_payload->>'dataset_ref')), '') AS dataset_ref,
            lower(NULLIF(btrim(coalesce(raw_payload->>'v_dataset', raw_payload->>'dataset_ref')), '')) AS norm_dataset_ref
        FROM catalog_object_staging
        WHERE export_id = :export_id
          AND object_type = 'Usage'
          AND is_graph_eligible
        """,
        """
        CREATE TEMP TABLE migration_v2_source_app_tmp ON COMMIT DROP AS
        SELECT node_id, app_code, lower(NULLIF(btrim(app_code), '')) AS norm_app_code
        FROM catalog_object_staging
        WHERE export_id = :export_id
          AND object_type = 'Source'
          AND is_graph_eligible
          AND app_code IS NOT NULL
          AND btrim(app_code) <> ''
        """,
        """
        CREATE TEMP TABLE migration_v2_structure_name_tmp ON COMMIT DROP AS
        SELECT node_id, name_label AS matched_value, lower(NULLIF(btrim(name_label), '')) AS norm_dataset_ref
        FROM catalog_object_staging
        WHERE export_id = :export_id
          AND object_type = 'Structure'
          AND is_graph_eligible
          AND name_label IS NOT NULL
          AND btrim(name_label) <> ''
        UNION ALL
        SELECT node_id, name_tech AS matched_value, lower(NULLIF(btrim(name_tech), '')) AS norm_dataset_ref
        FROM catalog_object_staging
        WHERE export_id = :export_id
          AND object_type = 'Structure'
          AND is_graph_eligible
          AND name_tech IS NOT NULL
          AND btrim(name_tech) <> ''
        """,
        "CREATE INDEX migration_v2_usage_ref_app_idx ON migration_v2_usage_ref_tmp(norm_app_code)",
        "CREATE INDEX migration_v2_usage_ref_dataset_idx ON migration_v2_usage_ref_tmp(norm_dataset_ref)",
        "CREATE INDEX migration_v2_source_app_idx ON migration_v2_source_app_tmp(norm_app_code)",
        "CREATE INDEX migration_v2_structure_name_idx ON migration_v2_structure_name_tmp(norm_dataset_ref)",
    ]
    for statement in statements:
        conn.execute(text(statement), {"export_id": export_id})


def _insert_app_code_relationships(conn, export_id: str) -> int:
    result = conn.execute(
        text(
            """
            INSERT INTO catalog_relationship_staging(
                export_id, src_node_id, tgt_node_id, relationship_type, relationship_family,
                source_table, link_type, status, raw_payload, unknown_columns,
                is_graph_eligible, graph_exclusion_reason
            )
            SELECT DISTINCT
                :export_id,
                u.node_id,
                s.node_id,
                'USES',
                'usage',
                'usage_resolver',
                'usage_app_code',
                u.status,
                jsonb_build_object(
                    'match_method', 'app_code',
                    'usage_app_code', u.app_code,
                    'source_app_code', s.app_code,
                    'rule', 'Usage.app_code = Source.app_code'
                ),
                '{}'::jsonb,
                true,
                NULL
            FROM migration_v2_usage_ref_tmp u
            JOIN migration_v2_source_app_tmp s
              ON s.norm_app_code = u.norm_app_code
            WHERE u.norm_app_code IS NOT NULL
            """
        ),
        {"export_id": export_id},
    )
    return int(result.rowcount or 0)


def _insert_dataset_exact_relationships(conn, export_id: str) -> int:
    result = conn.execute(
        text(
            """
            INSERT INTO catalog_relationship_staging(
                export_id, src_node_id, tgt_node_id, relationship_type, relationship_family,
                source_table, link_type, status, raw_payload, unknown_columns,
                is_graph_eligible, graph_exclusion_reason
            )
            SELECT DISTINCT
                :export_id,
                u.node_id,
                st.node_id,
                'USES',
                'usage',
                'usage_resolver',
                'usage_dataset_ref_exact',
                u.status,
                jsonb_build_object(
                    'match_method', 'dataset_ref_exact',
                    'dataset_ref', u.dataset_ref,
                    'matched_structure_name', st.matched_value,
                    'rule', 'Usage.dataset_ref equals Structure.name_label or Structure.name_tech'
                ),
                '{}'::jsonb,
                true,
                NULL
            FROM migration_v2_usage_ref_tmp u
            JOIN migration_v2_structure_name_tmp st
              ON st.norm_dataset_ref = u.norm_dataset_ref
            WHERE u.norm_dataset_ref IS NOT NULL
            """
        ),
        {"export_id": export_id},
    )
    return int(result.rowcount or 0)


def _insert_dataset_path_relationships(conn, export_id: str, path_match_limit: int) -> int:
    if path_match_limit <= 0:
        return 0
    result = conn.execute(
        text(
            """
            INSERT INTO catalog_relationship_staging(
                export_id, src_node_id, tgt_node_id, relationship_type, relationship_family,
                source_table, link_type, status, raw_payload, unknown_columns,
                is_graph_eligible, graph_exclusion_reason
            )
            SELECT DISTINCT
                :export_id,
                u.node_id,
                st.node_id,
                'USES',
                'usage',
                'usage_resolver',
                'usage_dataset_ref_path_contains',
                u.status,
                jsonb_build_object(
                    'match_method', 'dataset_ref_path_contains',
                    'dataset_ref', u.dataset_ref,
                    'target_path_full', st.path_full,
                    'path_match_limit', :path_match_limit,
                    'rule', 'Structure.path_full contains Usage.dataset_ref'
                ),
                '{}'::jsonb,
                true,
                NULL
            FROM migration_v2_usage_ref_tmp u
            CROSS JOIN LATERAL (
                SELECT node_id, path_full
                FROM catalog_object_staging st
                WHERE st.export_id = :export_id
                  AND st.object_type = 'Structure'
                  AND st.is_graph_eligible
                  AND st.path_full IS NOT NULL
                  AND position(lower(u.dataset_ref) in lower(st.path_full)) > 0
                ORDER BY
                    CASE
                        WHEN lower(st.name_label) = lower(u.dataset_ref)
                          OR lower(st.name_tech) = lower(u.dataset_ref)
                        THEN 0 ELSE 1
                    END,
                    length(st.path_full),
                    st.node_id
                LIMIT :path_match_limit
            ) st
            WHERE u.norm_dataset_ref IS NOT NULL
            """
        ),
        {"export_id": export_id, "path_match_limit": path_match_limit},
    )
    return int(result.rowcount or 0)


def _deduplicate_resolver_relationships(conn, export_id: str) -> int:
    result = conn.execute(
        text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY export_id, src_node_id, tgt_node_id, relationship_type
                        ORDER BY
                            CASE link_type
                                WHEN 'usage_app_code' THEN 0
                                WHEN 'usage_dataset_ref_exact' THEN 1
                                ELSE 2
                            END,
                            id
                    ) AS rn
                FROM catalog_relationship_staging
                WHERE export_id = :export_id
                  AND source_table = 'usage_resolver'
            )
            DELETE FROM catalog_relationship_staging rel
            USING ranked
            WHERE rel.id = ranked.id
              AND ranked.rn > 1
            """
        ),
        {"export_id": export_id},
    )
    return int(result.rowcount or 0)


def _collect_counts(conn, export_id: str) -> dict[str, int]:
    rows = conn.execute(
        text(
            """
            SELECT link_type, count(*) AS count
            FROM catalog_relationship_staging
            WHERE export_id = :export_id
              AND source_table = 'usage_resolver'
            GROUP BY link_type
            ORDER BY link_type
            """
        ),
        {"export_id": export_id},
    ).mappings().all()
    return {str(row["link_type"]): int(row["count"]) for row in rows}


def resolve_usage_relationships(
    engine: Engine,
    export_id: str,
    dataset_path_match_limit: int = 0,
) -> dict[str, Any]:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM catalog_relationship_staging
                WHERE export_id = :export_id
                  AND source_table = 'usage_resolver'
                """
            ),
            {"export_id": export_id},
        )
        _prepare_lookup_tables(conn, export_id)
        inserted_app_code = _insert_app_code_relationships(conn, export_id)
        inserted_dataset_exact = _insert_dataset_exact_relationships(conn, export_id)
        inserted_dataset_path = _insert_dataset_path_relationships(conn, export_id, dataset_path_match_limit)
        duplicates_removed = _deduplicate_resolver_relationships(conn, export_id)
        counts = _collect_counts(conn, export_id)

    return {
        "export_id": export_id,
        "status": "completed",
        "dataset_path_match_limit": dataset_path_match_limit,
        "inserted_before_dedup": {
            "usage_app_code": inserted_app_code,
            "usage_dataset_ref_exact": inserted_dataset_exact,
            "usage_dataset_ref_path_contains": inserted_dataset_path,
        },
        "duplicates_removed": duplicates_removed,
        "relationship_counts": counts,
        "total_relationships": sum(counts.values()),
    }
