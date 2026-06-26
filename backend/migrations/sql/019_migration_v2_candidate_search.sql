CREATE OR REPLACE FUNCTION refresh_migration_v2_candidate_search_documents(p_export_id text)
RETURNS TABLE(graph_version bigint, document_count bigint)
LANGUAGE plpgsql
AS $$
DECLARE
    next_version bigint;
    next_count bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(821704119);
    TRUNCATE TABLE lineage_search_document;

    INSERT INTO lineage_search_document(
        node_id, entity_level, source_table, label, technical_name, path_full,
        parent_node_id, normalized_label, normalized_technical_name,
        normalized_path, search_text
    )
    SELECT node_id,
           object_type,
           source_table,
           name_label,
           name_tech,
           path_full,
           parent_node_id,
           lineage_search_normalize(name_label),
           lineage_search_normalize(name_tech),
           lineage_search_normalize(path_full),
           lineage_search_text(node_id, name_label, name_tech, path_full)
    FROM migration_trusted_object_projection
    WHERE export_id = p_export_id AND node_id <> '';

    GET DIAGNOSTICS next_count = ROW_COUNT;
    INSERT INTO lineage_search_state(singleton) VALUES (true) ON CONFLICT DO NOTHING;
    SELECT active_graph_version + 1 INTO next_version
    FROM lineage_search_state WHERE singleton = true FOR UPDATE;
    UPDATE lineage_search_state
    SET active_graph_version = next_version,
        document_count = next_count,
        published_at = now()
    WHERE singleton = true;
    RETURN QUERY SELECT next_version, next_count;
END
$$;
