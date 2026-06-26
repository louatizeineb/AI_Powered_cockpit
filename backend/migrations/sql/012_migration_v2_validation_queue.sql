CREATE TABLE IF NOT EXISTS migration_validation_queue (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    issue_id text NOT NULL,
    issue_type text NOT NULL,
    entity_kind text NOT NULL,
    node_id text,
    src_node_id text,
    tgt_node_id text,
    relationship_type text,
    severity text NOT NULL,
    confidence numeric(5, 4),
    publish_policy text NOT NULL,
    queue_status text NOT NULL DEFAULT 'pending',
    source_report text,
    source_decision_status text,
    proposed_by text NOT NULL DEFAULT 'deterministic_queue_builder',
    proposed_action text NOT NULL,
    rationale text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    approved_by text,
    approved_at timestamptz,
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (export_id, issue_id)
);

CREATE INDEX IF NOT EXISTS idx_migration_validation_queue_export_policy
    ON migration_validation_queue(export_id, publish_policy, queue_status);

CREATE INDEX IF NOT EXISTS idx_migration_validation_queue_export_type
    ON migration_validation_queue(export_id, issue_type);

CREATE INDEX IF NOT EXISTS idx_migration_validation_queue_node
    ON migration_validation_queue(export_id, node_id);

CREATE INDEX IF NOT EXISTS idx_migration_validation_queue_relationship
    ON migration_validation_queue(export_id, relationship_type);

CREATE INDEX IF NOT EXISTS idx_migration_validation_queue_severity
    ON migration_validation_queue(export_id, severity);
