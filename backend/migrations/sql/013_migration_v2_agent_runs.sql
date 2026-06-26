CREATE TABLE IF NOT EXISTS migration_agent_run (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    agent_name text NOT NULL,
    mode text NOT NULL,
    model_name text,
    status text NOT NULL,
    requested_limit integer,
    reviewed_count integer NOT NULL DEFAULT 0,
    proposal_count integer NOT NULL DEFAULT 0,
    llm_call_count integer NOT NULL DEFAULT 0,
    fallback_count integer NOT NULL DEFAULT 0,
    errors jsonb NOT NULL DEFAULT '[]'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS migration_agent_proposal (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    run_id bigint NOT NULL REFERENCES migration_agent_run(id) ON DELETE CASCADE,
    agent_name text NOT NULL,
    issue_id text NOT NULL,
    issue_type text,
    proposed_policy text NOT NULL,
    confidence numeric(5, 4),
    rationale text NOT NULL,
    missing_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    human_question text,
    guardrail_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw_model_response text,
    fallback_used boolean NOT NULL DEFAULT false,
    applied_to_queue boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (export_id, run_id, issue_id)
);

CREATE INDEX IF NOT EXISTS idx_migration_agent_run_export_agent
    ON migration_agent_run(export_id, agent_name, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_migration_agent_proposal_export_issue
    ON migration_agent_proposal(export_id, issue_id);

CREATE INDEX IF NOT EXISTS idx_migration_agent_proposal_policy
    ON migration_agent_proposal(export_id, proposed_policy);
