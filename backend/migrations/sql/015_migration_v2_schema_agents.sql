ALTER TABLE migration_agent_run
    ADD COLUMN IF NOT EXISTS workflow_run_id uuid REFERENCES migration_workflow_run(run_id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS migration_schema_mapping_proposal (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    workflow_run_id uuid NOT NULL REFERENCES migration_workflow_run(run_id) ON DELETE CASCADE,
    agent_run_id bigint NOT NULL REFERENCES migration_agent_run(id) ON DELETE CASCADE,
    raw_table_name text NOT NULL,
    raw_column_name text NOT NULL,
    current_canonical_field text,
    proposed_canonical_field text,
    proposed_action text NOT NULL,
    confidence numeric(5, 4) NOT NULL,
    rationale text NOT NULL,
    missing_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    human_question text,
    candidate_columns jsonb NOT NULL DEFAULT '[]'::jsonb,
    guardrail_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw_model_response text,
    status text NOT NULL DEFAULT 'pending',
    approved_by text,
    approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (agent_run_id, raw_table_name, raw_column_name),
    CHECK (proposed_action IN ('keep_contract_missing', 'deprecate_contract_column', 'map_to_observed_column', 'needs_human')),
    CHECK (status IN ('pending', 'approved', 'rejected', 'superseded'))
);

CREATE INDEX IF NOT EXISTS idx_schema_mapping_proposal_export_status
    ON migration_schema_mapping_proposal(export_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_migration_agent_run_workflow
    ON migration_agent_run(workflow_run_id, started_at DESC);
