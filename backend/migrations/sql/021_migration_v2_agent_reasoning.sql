CREATE TABLE IF NOT EXISTS migration_agent_evidence_plan (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    run_id bigint NOT NULL REFERENCES migration_agent_run(id) ON DELETE CASCADE,
    agent_name text NOT NULL,
    issue_id text NOT NULL,
    issue_type text,
    objective text NOT NULL,
    required_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    planned_queries jsonb NOT NULL DEFAULT '[]'::jsonb,
    planned_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
    repair_strategy text NOT NULL DEFAULT '',
    risk_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (export_id, run_id, issue_id)
);

CREATE INDEX IF NOT EXISTS idx_migration_agent_evidence_plan_export_issue
    ON migration_agent_evidence_plan(export_id, issue_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_migration_agent_evidence_plan_run
    ON migration_agent_evidence_plan(run_id);
