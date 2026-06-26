CREATE TABLE IF NOT EXISTS migration_role_resolution (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    node_id text NOT NULL,
    observed_roles jsonb NOT NULL DEFAULT '[]'::jsonb,
    canonical_role text,
    retained_roles jsonb NOT NULL DEFAULT '[]'::jsonb,
    conflict_fields jsonb NOT NULL DEFAULT '[]'::jsonb,
    decision_status text NOT NULL,
    decision_reason text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (export_id, node_id)
);

CREATE TABLE IF NOT EXISTS migration_orphan_classification (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    node_id text NOT NULL,
    object_type text,
    orphan_class text NOT NULL,
    decision_status text NOT NULL,
    decision_reason text NOT NULL,
    child_count bigint NOT NULL DEFAULT 0,
    relationship_count bigint NOT NULL DEFAULT 0,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (export_id, node_id)
);

CREATE TABLE IF NOT EXISTS migration_relationship_explanation (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    relationship_type text NOT NULL,
    baseline_value numeric,
    v2_value numeric,
    delta_value numeric,
    parity_status text NOT NULL,
    decision_status text NOT NULL,
    explanation_class text NOT NULL,
    inverse_relationship_type text,
    raw_link_types jsonb NOT NULL DEFAULT '[]'::jsonb,
    decision_reason text NOT NULL,
    required_action text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (export_id, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_migration_role_resolution_export_status
    ON migration_role_resolution(export_id, decision_status);

CREATE INDEX IF NOT EXISTS idx_migration_role_resolution_node
    ON migration_role_resolution(export_id, node_id);

CREATE INDEX IF NOT EXISTS idx_migration_orphan_classification_export_status
    ON migration_orphan_classification(export_id, decision_status);

CREATE INDEX IF NOT EXISTS idx_migration_orphan_classification_class
    ON migration_orphan_classification(export_id, orphan_class);

CREATE INDEX IF NOT EXISTS idx_migration_relationship_explanation_export_status
    ON migration_relationship_explanation(export_id, decision_status);

CREATE INDEX IF NOT EXISTS idx_migration_relationship_explanation_type
    ON migration_relationship_explanation(export_id, relationship_type);
