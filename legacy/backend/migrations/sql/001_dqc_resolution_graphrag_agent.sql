CREATE TABLE IF NOT EXISTS catalog_path_index (
    id BIGSERIAL PRIMARY KEY,
    entity_table TEXT NOT NULL,
    entity_level TEXT NOT NULL,
    node_id TEXT NOT NULL,
    raw_path_full TEXT,
    normalized_path TEXT,
    app_code_from_path TEXT,
    leaf_name TEXT,
    parent_name TEXT,
    path_depth INTEGER,
    path_segments TEXT[],
    path_tokens TEXT[],
    created_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_catalog_path_app ON catalog_path_index(app_code_from_path);
CREATE INDEX IF NOT EXISTS idx_catalog_path_level ON catalog_path_index(entity_level);
CREATE INDEX IF NOT EXISTS idx_catalog_path_leaf ON catalog_path_index(leaf_name);
CREATE INDEX IF NOT EXISTS idx_catalog_path_tokens ON catalog_path_index USING GIN(path_tokens);

CREATE TABLE IF NOT EXISTS catalog_node_embeddings (
    id BIGSERIAL PRIMARY KEY,
    catalog_path_index_id BIGINT NOT NULL REFERENCES catalog_path_index(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    entity_level TEXT NOT NULL,
    embedding_text TEXT NOT NULL,
    embedding_vector DOUBLE PRECISION[] NOT NULL,
    model_name TEXT NOT NULL,
    generated_at TIMESTAMP DEFAULT now(),
    UNIQUE(catalog_path_index_id, model_name)
);

CREATE INDEX IF NOT EXISTS idx_catalog_embeddings_node ON catalog_node_embeddings(node_id);
CREATE INDEX IF NOT EXISTS idx_catalog_embeddings_level ON catalog_node_embeddings(entity_level);

CREATE TABLE IF NOT EXISTS dqc_raw (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT,
    source_system TEXT,
    raw_payload JSONB NOT NULL,
    received_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dqc_normalized (
    id BIGSERIAL PRIMARY KEY,
    raw_id BIGINT REFERENCES dqc_raw(id),
    raw_dqc_id TEXT,
    source_system TEXT,
    application_code_raw TEXT,
    application_code_norm TEXT,
    controlled_object_name_raw TEXT,
    controlled_source_name_raw TEXT,
    controlled_structure_name TEXT,
    controlled_field_name TEXT,
    target_level TEXT,
    quality_dimension TEXT,
    control_name TEXT,
    control_tool TEXT,
    cdq_profile TEXT,
    control_link TEXT,
    acceptance_threshold DOUBLE PRECISION,
    controlled_item_count BIGINT,
    ok_count BIGINT,
    ko_count BIGINT,
    ko_rate DOUBLE PRECISION,
    quality_score DOUBLE PRECISION,
    normalized_payload JSONB,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dqc_match_candidate (
    id BIGSERIAL PRIMARY KEY,
    normalized_id BIGINT REFERENCES dqc_normalized(id),
    candidate_node_id TEXT,
    candidate_entity_level TEXT,
    candidate_path_full TEXT,
    match_method TEXT,
    match_score DOUBLE PRECISION,
    match_reasons JSONB,
    rank INTEGER,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dqc_resolved (
    id BIGSERIAL PRIMARY KEY,
    normalized_id BIGINT REFERENCES dqc_normalized(id),
    matched_node_id TEXT,
    matched_entity_level TEXT,
    matched_path_full TEXT,
    match_method TEXT,
    match_score DOUBLE PRECISION,
    confidence_level TEXT,
    human_review_required BOOLEAN DEFAULT false,
    reviewed BOOLEAN DEFAULT false,
    reviewed_by TEXT,
    reviewed_at TIMESTAMP,
    review_note TEXT,
    resolution_status TEXT,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dqc_dlq (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT,
    raw_id BIGINT REFERENCES dqc_raw(id),
    normalized_id BIGINT REFERENCES dqc_normalized(id),
    failure_stage TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    failure_details JSONB,
    llm_analysis TEXT,
    suggested_action TEXT,
    reviewed BOOLEAN DEFAULT false,
    reviewed_by TEXT,
    reviewed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pipeline_logs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT,
    stage TEXT,
    level TEXT,
    message TEXT,
    details JSONB,
    created_at TIMESTAMP DEFAULT now()
);
