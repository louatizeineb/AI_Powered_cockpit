ALTER TABLE migration_schema_mapping_proposal
    ADD COLUMN IF NOT EXISTS reviewer_action text,
    ADD COLUMN IF NOT EXISTS reviewer_rationale text,
    ADD COLUMN IF NOT EXISTS reviewed_by text,
    ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_schema_mapping_proposal_workflow_pending
    ON migration_schema_mapping_proposal(workflow_run_id, status, raw_table_name, raw_column_name);
