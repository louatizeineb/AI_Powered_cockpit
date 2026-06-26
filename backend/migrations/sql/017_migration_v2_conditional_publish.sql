DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'migration_publication_state') THEN
        CREATE TYPE migration_publication_state AS ENUM (
            'trusted', 'quarantine', 'excluded', 'repair', 'hard_block', 'review_pending'
        );
    END IF;
END
$$;

ALTER TABLE catalog_object_staging
    ADD COLUMN IF NOT EXISTS publication_state migration_publication_state NOT NULL DEFAULT 'review_pending',
    ADD COLUMN IF NOT EXISTS publication_reason text,
    ADD COLUMN IF NOT EXISTS publication_policy_version text,
    ADD COLUMN IF NOT EXISTS publication_decided_by text,
    ADD COLUMN IF NOT EXISTS publication_decided_at timestamptz,
    ADD COLUMN IF NOT EXISTS publication_evidence jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE catalog_relationship_staging
    ADD COLUMN IF NOT EXISTS publication_state migration_publication_state NOT NULL DEFAULT 'review_pending',
    ADD COLUMN IF NOT EXISTS publication_reason text,
    ADD COLUMN IF NOT EXISTS publication_policy_version text,
    ADD COLUMN IF NOT EXISTS publication_decided_by text,
    ADD COLUMN IF NOT EXISTS publication_decided_at timestamptz,
    ADD COLUMN IF NOT EXISTS publication_evidence jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE migration_tool_execution
    ADD COLUMN IF NOT EXISTS database_effects jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS migration_publication_snapshot (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    policy_version text NOT NULL,
    status text NOT NULL,
    object_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    relationship_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    hard_blockers jsonb NOT NULL DEFAULT '[]'::jsonb,
    rollback_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('ready', 'blocked', 'published', 'rolled_back'))
);

CREATE OR REPLACE VIEW migration_trusted_object_projection AS
SELECT * FROM catalog_object_staging
WHERE is_graph_eligible
  AND publication_state IN ('trusted', 'review_pending');

CREATE OR REPLACE VIEW migration_quarantine_object_projection AS
SELECT * FROM catalog_object_staging WHERE publication_state = 'quarantine';

CREATE OR REPLACE VIEW migration_trusted_relationship_projection AS
SELECT relationship.*
FROM catalog_relationship_staging relationship
WHERE relationship.is_graph_eligible
  AND relationship.publication_state IN ('trusted', 'review_pending')
  AND EXISTS (
      SELECT 1 FROM catalog_object_staging source
      WHERE source.export_id = relationship.export_id
        AND source.node_id = relationship.src_node_id
        AND source.is_graph_eligible
        AND source.publication_state IN ('trusted', 'review_pending')
  )
  AND EXISTS (
      SELECT 1 FROM catalog_object_staging target
      WHERE target.export_id = relationship.export_id
        AND target.node_id = relationship.tgt_node_id
        AND target.is_graph_eligible
        AND target.publication_state IN ('trusted', 'review_pending')
  );

CREATE OR REPLACE VIEW migration_quarantine_relationship_projection AS
SELECT *
FROM catalog_relationship_staging
WHERE publication_state = 'quarantine';

CREATE INDEX IF NOT EXISTS idx_catalog_object_publication_state
    ON catalog_object_staging(export_id, publication_state);
CREATE INDEX IF NOT EXISTS idx_catalog_relationship_publication_state
    ON catalog_relationship_staging(export_id, publication_state);
CREATE INDEX IF NOT EXISTS idx_migration_publication_snapshot_export
    ON migration_publication_snapshot(export_id, created_at DESC);

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION lineage_search_normalize(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT trim(both '_' FROM regexp_replace(
        lower(public.unaccent('public.unaccent', coalesce(value, ''))),
        '[^a-z0-9]+', '_', 'g'
    ))
$$;

CREATE OR REPLACE FUNCTION lineage_search_text(
    node_id text, label text, technical_name text, path_full text
)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT concat_ws(
        ' ', lower(coalesce(node_id, '')),
        lineage_search_normalize(label), replace(lineage_search_normalize(label), '_', ' '),
        lineage_search_normalize(technical_name), replace(lineage_search_normalize(technical_name), '_', ' '),
        lineage_search_normalize(path_full), replace(lineage_search_normalize(path_full), '_', ' ')
    )
$$;

CREATE TABLE IF NOT EXISTS lineage_search_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active_graph_version bigint NOT NULL DEFAULT 0,
    document_count bigint NOT NULL DEFAULT 0,
    published_at timestamptz
);
INSERT INTO lineage_search_state(singleton) VALUES (true) ON CONFLICT DO NOTHING;

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

CREATE INDEX IF NOT EXISTS idx_lineage_search_document_search_text_trgm
    ON lineage_search_document USING gin (search_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_lineage_search_document_search_tsv
    ON lineage_search_document USING gin (search_tsv);

CREATE OR REPLACE FUNCTION refresh_lineage_search_documents(p_export_id text)
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

    INSERT INTO lineage_search_stage
    SELECT DISTINCT ON (node_id, object_type)
        node_id,
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
    WHERE export_id = p_export_id AND node_id <> ''
    ORDER BY node_id, object_type, path_full DESC NULLS LAST;

    DELETE FROM lineage_search_document document
    WHERE NOT EXISTS (
        SELECT 1 FROM lineage_search_stage stage
        WHERE stage.node_id = document.node_id AND stage.entity_level = document.entity_level
    );

    INSERT INTO lineage_search_document(
        node_id, entity_level, source_table, label, technical_name, path_full,
        parent_node_id, normalized_label, normalized_technical_name, normalized_path, search_text
    )
    SELECT node_id, entity_level, source_table, label, technical_name, path_full,
           parent_node_id, normalized_label, normalized_technical_name, normalized_path, search_text
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
        search_text = EXCLUDED.search_text;

    SELECT count(*) INTO next_count FROM lineage_search_stage;
    INSERT INTO lineage_search_state(singleton) VALUES (true) ON CONFLICT DO NOTHING;
    SELECT active_graph_version + 1 INTO next_version
    FROM lineage_search_state WHERE singleton = true FOR UPDATE;
    UPDATE lineage_search_state
    SET active_graph_version = next_version, document_count = next_count, published_at = now()
    WHERE singleton = true;
    RETURN QUERY SELECT next_version, next_count;
END
$$;
