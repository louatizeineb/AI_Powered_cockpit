CREATE TABLE IF NOT EXISTS migration_export_run (
    export_id text PRIMARY KEY,
    export_path text,
    contract_version text,
    status text NOT NULL DEFAULT 'registered',
    baseline_status text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS migration_raw_file (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    raw_table_name text NOT NULL,
    file_path text NOT NULL,
    file_hash text,
    row_count bigint,
    column_count integer,
    detected_format text,
    columns jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (export_id, raw_table_name, file_path)
);

CREATE TABLE IF NOT EXISTS migration_column_profile (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    raw_table_name text NOT NULL,
    column_name text NOT NULL,
    data_type_guess text,
    null_count bigint,
    distinct_count bigint,
    non_null_count bigint,
    sample_values jsonb NOT NULL DEFAULT '[]'::jsonb,
    warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (export_id, raw_table_name, column_name)
);

CREATE TABLE IF NOT EXISTS migration_mapping_decision (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    contract_version text,
    raw_table_name text NOT NULL,
    raw_column_name text,
    canonical_field text,
    decision_type text NOT NULL,
    confidence numeric(5, 4),
    requires_human_approval boolean NOT NULL DEFAULT false,
    approved_by text,
    approved_at timestamptz,
    rationale text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog_object_staging (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    node_id text NOT NULL,
    parent_node_id text,
    object_type text NOT NULL,
    name_label text,
    name_tech text,
    path_full text,
    path_hash text,
    entity_type text,
    data_type text,
    status text,
    app_code text,
    source_table text NOT NULL,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    unknown_columns jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_graph_eligible boolean NOT NULL DEFAULT false,
    graph_exclusion_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (export_id, node_id, object_type)
);

CREATE TABLE IF NOT EXISTS catalog_relationship_staging (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    src_node_id text NOT NULL,
    tgt_node_id text NOT NULL,
    relationship_type text NOT NULL,
    relationship_family text,
    source_table text NOT NULL,
    link_type text,
    status text,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    unknown_columns jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_graph_eligible boolean NOT NULL DEFAULT false,
    graph_exclusion_reason text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS migration_validation_finding (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    severity text NOT NULL,
    category text NOT NULL,
    entity_type text,
    node_id text,
    relationship_id bigint,
    message text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'open',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lineage_path (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    graph_version bigint,
    start_node_id text NOT NULL,
    end_node_id text NOT NULL,
    path_hash text NOT NULL,
    path_nodes jsonb NOT NULL DEFAULT '[]'::jsonb,
    path_relationships jsonb NOT NULL DEFAULT '[]'::jsonb,
    path_length integer NOT NULL,
    path_family text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (export_id, graph_version, path_hash)
);

CREATE TABLE IF NOT EXISTS migration_benchmark_result (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    baseline_name text NOT NULL DEFAULT 'v0',
    metric_name text NOT NULL,
    baseline_value numeric,
    v2_value numeric,
    delta_value numeric,
    delta_pct numeric,
    status text NOT NULL DEFAULT 'recorded',
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_migration_raw_file_export_id
    ON migration_raw_file(export_id);

CREATE INDEX IF NOT EXISTS idx_migration_column_profile_export_id
    ON migration_column_profile(export_id);

CREATE INDEX IF NOT EXISTS idx_migration_column_profile_table
    ON migration_column_profile(export_id, raw_table_name);

CREATE INDEX IF NOT EXISTS idx_migration_mapping_decision_export_id
    ON migration_mapping_decision(export_id);

CREATE INDEX IF NOT EXISTS idx_catalog_object_staging_export_id
    ON catalog_object_staging(export_id);

CREATE INDEX IF NOT EXISTS idx_catalog_object_staging_node_id
    ON catalog_object_staging(node_id);

CREATE INDEX IF NOT EXISTS idx_catalog_object_staging_export_node_id
    ON catalog_object_staging(export_id, node_id);

CREATE INDEX IF NOT EXISTS idx_catalog_object_staging_path_hash
    ON catalog_object_staging(path_hash);

CREATE INDEX IF NOT EXISTS idx_catalog_object_staging_status
    ON catalog_object_staging(export_id, status);

CREATE INDEX IF NOT EXISTS idx_catalog_relationship_staging_export_id
    ON catalog_relationship_staging(export_id);

CREATE INDEX IF NOT EXISTS idx_catalog_relationship_staging_src
    ON catalog_relationship_staging(export_id, src_node_id);

CREATE INDEX IF NOT EXISTS idx_catalog_relationship_staging_tgt
    ON catalog_relationship_staging(export_id, tgt_node_id);

CREATE INDEX IF NOT EXISTS idx_catalog_relationship_staging_type
    ON catalog_relationship_staging(export_id, relationship_type);

CREATE INDEX IF NOT EXISTS idx_validation_finding_export_id
    ON migration_validation_finding(export_id);

CREATE INDEX IF NOT EXISTS idx_validation_finding_severity_category
    ON migration_validation_finding(export_id, severity, category);

CREATE INDEX IF NOT EXISTS idx_lineage_path_export_id
    ON lineage_path(export_id);

CREATE INDEX IF NOT EXISTS idx_lineage_path_start_node_id
    ON lineage_path(export_id, start_node_id);

CREATE INDEX IF NOT EXISTS idx_lineage_path_end_node_id
    ON lineage_path(export_id, end_node_id);

CREATE INDEX IF NOT EXISTS idx_lineage_path_family
    ON lineage_path(export_id, path_family);

CREATE INDEX IF NOT EXISTS idx_lineage_path_hash
    ON lineage_path(path_hash);

CREATE INDEX IF NOT EXISTS idx_lineage_path_nodes_gin
    ON lineage_path USING gin(path_nodes);

CREATE INDEX IF NOT EXISTS idx_benchmark_result_export_id
    ON migration_benchmark_result(export_id);

CREATE INDEX IF NOT EXISTS idx_benchmark_result_metric
    ON migration_benchmark_result(export_id, metric_name);
