CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION lineage_search_normalize(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT trim(
        both '_' FROM regexp_replace(
            lower(public.unaccent('public.unaccent', coalesce(value, ''))),
            '[^a-z0-9]+',
            '_',
            'g'
        )
    )
$$;

CREATE OR REPLACE FUNCTION lineage_search_text(
    node_id text,
    label text,
    technical_name text,
    path_full text
)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT concat_ws(
        ' ',
        lower(coalesce(node_id, '')),
        lineage_search_normalize(label),
        replace(lineage_search_normalize(label), '_', ' '),
        lineage_search_normalize(technical_name),
        replace(lineage_search_normalize(technical_name), '_', ' '),
        lineage_search_normalize(path_full),
        replace(lineage_search_normalize(path_full), '_', ' ')
    )
$$;

CREATE OR REPLACE FUNCTION lineage_search_entity_level(entity_type text, data_type text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN compact LIKE '%dataprocessingitem%' THEN 'DataProcessingItem'
        WHEN compact LIKE '%dataprocessing%' OR compact LIKE '%process%' THEN 'DataProcessing'
        WHEN compact LIKE '%businessterm%' THEN 'BusinessTerm'
        WHEN compact LIKE '%usage%' THEN 'Usage'
        WHEN compact LIKE '%field%' OR compact LIKE '%column%' OR compact LIKE '%attribut%' THEN 'Field'
        WHEN compact LIKE '%structure%' OR compact LIKE '%table%' THEN 'Structure'
        WHEN compact LIKE '%container%' THEN 'Container'
        WHEN compact LIKE '%source%' OR compact LIKE '%database%' THEN 'Source'
        ELSE 'LineageNode'
    END
    FROM (
        SELECT regexp_replace(lower(coalesce(entity_type, '') || ' ' || coalesce(data_type, '')), '[^a-z0-9]+', '', 'g') AS compact
    ) normalized
$$;

CREATE TABLE IF NOT EXISTS lineage_search_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active_graph_version bigint NOT NULL DEFAULT 0,
    document_count bigint NOT NULL DEFAULT 0,
    published_at timestamptz
);

INSERT INTO lineage_search_state(singleton)
VALUES (true)
ON CONFLICT (singleton) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'lineage_search_document'
          AND column_name = 'graph_version'
    ) THEN
        DROP TABLE lineage_search_document;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS lineage_search_document (
    node_id text NOT NULL,
    entity_level text NOT NULL,
    source_table text NOT NULL,
    label text,
    technical_name text,
    path_full text,
    parent_node_id text,
    normalized_label text NOT NULL DEFAULT '',
    normalized_technical_name text NOT NULL DEFAULT '',
    normalized_path text NOT NULL DEFAULT '',
    search_text text NOT NULL,
    search_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
    PRIMARY KEY (node_id, entity_level)
);

CREATE OR REPLACE FUNCTION refresh_lineage_search_documents()
RETURNS TABLE(graph_version bigint, document_count bigint)
LANGUAGE plpgsql
AS $$
DECLARE
    next_version bigint;
    next_count bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(821704119);

    CREATE TEMP TABLE lineage_search_stage (
        node_id text NOT NULL,
        entity_level text NOT NULL,
        source_table text NOT NULL,
        label text,
        technical_name text,
        path_full text,
        parent_node_id text,
        normalized_label text NOT NULL DEFAULT '',
        normalized_technical_name text NOT NULL DEFAULT '',
        normalized_path text NOT NULL DEFAULT '',
        search_text text NOT NULL,
        PRIMARY KEY (node_id, entity_level)
    ) ON COMMIT DROP;

    INSERT INTO lineage_search_stage(
        node_id, entity_level, source_table, label, technical_name,
        path_full, parent_node_id, normalized_label, normalized_technical_name,
        normalized_path, search_text
    )
    SELECT DISTINCT ON (node_id, entity_level)
           node_id, entity_level, source_table, label, technical_name,
           path_full, parent_node_id, lineage_search_normalize(label),
           lineage_search_normalize(technical_name), lineage_search_normalize(path_full),
           lineage_search_text(node_id, label, technical_name, path_full)
    FROM (
        SELECT node_id, 'Source'::text AS entity_level, 'source'::text AS source_table,
               name_label AS label, name_tech AS technical_name, path_full, NULL::text AS parent_node_id
        FROM source WHERE node_id IS NOT NULL AND node_id <> ''
        UNION ALL
        SELECT node_id, 'Container', 'container', name_label, name_tech, path_full, parent_node_id
        FROM container WHERE node_id IS NOT NULL AND node_id <> ''
        UNION ALL
        SELECT node_id, 'Structure', 'structure', name_label, name_tech, path_full, parent_node_id
        FROM structure WHERE node_id IS NOT NULL AND node_id <> ''
        UNION ALL
        SELECT node_id, 'Field', 'field', name_label, name_tech, path_full, parent_node_id
        FROM field WHERE node_id IS NOT NULL AND node_id <> ''
        UNION ALL
        SELECT usage_uuid, 'Usage', 'usage', usage_name, usage_tech_name, usage_path, parent_uuid
        FROM usage WHERE usage_uuid IS NOT NULL AND usage_uuid <> ''
    ) catalog
    ORDER BY node_id, entity_level, path_full DESC NULLS LAST;

    INSERT INTO lineage_search_stage(
        node_id, entity_level, source_table, label, technical_name,
        path_full, parent_node_id, normalized_label, normalized_technical_name,
        normalized_path, search_text
    )
    SELECT node_id, entity_level, 'link', label, technical_name,
           path_full, NULL::text, lineage_search_normalize(label),
           lineage_search_normalize(technical_name), lineage_search_normalize(path_full),
           lineage_search_text(node_id, label, technical_name, path_full)
    FROM (
        SELECT DISTINCT ON (node_id, entity_level)
               node_id, entity_level, label, technical_name, path_full
        FROM (
            SELECT src_node_id AS node_id,
                   lineage_search_entity_level(src_entity_type, src_data_type) AS entity_level,
                   src_name_label AS label, src_name_tech AS technical_name, NULL::text AS path_full
            FROM link WHERE src_node_id IS NOT NULL AND src_node_id <> ''
            UNION ALL
            SELECT tgt_node_id,
                   lineage_search_entity_level(tgt_entity_type, tgt_data_type),
                   tgt_name_label, tgt_name_tech, tgt_path
            FROM link WHERE tgt_node_id IS NOT NULL AND tgt_node_id <> ''
        ) endpoints
        ORDER BY node_id, entity_level, path_full DESC NULLS LAST
    ) lineage
    ON CONFLICT (node_id, entity_level) DO UPDATE
    SET label = coalesce(NULLIF(lineage_search_stage.label, ''), EXCLUDED.label),
        technical_name = coalesce(NULLIF(lineage_search_stage.technical_name, ''), EXCLUDED.technical_name),
        path_full = coalesce(NULLIF(lineage_search_stage.path_full, ''), EXCLUDED.path_full),
        search_text = concat_ws(' ', lineage_search_stage.search_text, EXCLUDED.search_text);

    DELETE FROM lineage_search_document document
    WHERE NOT EXISTS (
        SELECT 1
        FROM lineage_search_stage stage
        WHERE stage.node_id = document.node_id
          AND stage.entity_level = document.entity_level
    );

    INSERT INTO lineage_search_document(
        node_id, entity_level, source_table, label, technical_name, path_full,
        parent_node_id, normalized_label, normalized_technical_name, normalized_path,
        search_text
    )
    SELECT node_id, entity_level, source_table, label, technical_name, path_full,
           parent_node_id, normalized_label, normalized_technical_name, normalized_path,
           search_text
    FROM lineage_search_stage
    ON CONFLICT (node_id, entity_level) DO UPDATE
    SET source_table = EXCLUDED.source_table,
        label = EXCLUDED.label,
        technical_name = EXCLUDED.technical_name,
        path_full = EXCLUDED.path_full,
        parent_node_id = EXCLUDED.parent_node_id,
        normalized_label = EXCLUDED.normalized_label,
        normalized_technical_name = EXCLUDED.normalized_technical_name,
        normalized_path = EXCLUDED.normalized_path,
        search_text = EXCLUDED.search_text
    WHERE (
        lineage_search_document.source_table,
        lineage_search_document.label,
        lineage_search_document.technical_name,
        lineage_search_document.path_full,
        lineage_search_document.parent_node_id,
        lineage_search_document.search_text
    ) IS DISTINCT FROM (
        EXCLUDED.source_table,
        EXCLUDED.label,
        EXCLUDED.technical_name,
        EXCLUDED.path_full,
        EXCLUDED.parent_node_id,
        EXCLUDED.search_text
    );

    SELECT count(*) INTO next_count FROM lineage_search_stage;

    INSERT INTO lineage_search_state(singleton)
    VALUES (true)
    ON CONFLICT (singleton) DO NOTHING;

    SELECT active_graph_version + 1
    INTO next_version
    FROM lineage_search_state
    WHERE singleton = true
    FOR UPDATE;

    UPDATE lineage_search_state
    SET active_graph_version = next_version,
        document_count = next_count,
        published_at = now()
    WHERE singleton = true;

    RETURN QUERY SELECT next_version, next_count;
END
$$;
