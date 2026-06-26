CREATE TABLE IF NOT EXISTS dqc_agent_run (
    id BIGSERIAL PRIMARY KEY,
    agent_name text NOT NULL,
    mode text NOT NULL,
    status text NOT NULL DEFAULT 'running',
    source text NOT NULL DEFAULT 'api',
    message text,
    reviewed_count integer NOT NULL DEFAULT 0,
    proposal_count integer NOT NULL DEFAULT 0,
    llm_call_count integer NOT NULL DEFAULT 0,
    fallback_count integer NOT NULL DEFAULT 0,
    errors jsonb NOT NULL DEFAULT '[]'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS dqc_agent_proposal (
    id BIGSERIAL PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES dqc_agent_run(id) ON DELETE CASCADE,
    agent_name text NOT NULL,
    subject_type text NOT NULL,
    subject_id bigint,
    proposed_action text NOT NULL,
    confidence text,
    rationale text NOT NULL,
    missing_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    human_question text,
    guardrail_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_model_response text,
    applied_to_review boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (subject_type IN ('workflow_result', 'resolved', 'dlq')),
    CHECK (proposed_action IN ('approve_match', 'reject_match', 'keep_in_dlq', 'search_alternatives', 'replay_after_fix'))
);

CREATE INDEX IF NOT EXISTS idx_dqc_agent_run_agent_started
    ON dqc_agent_run(agent_name, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_dqc_agent_proposal_subject
    ON dqc_agent_proposal(subject_type, subject_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_dqc_agent_proposal_action
    ON dqc_agent_proposal(proposed_action, created_at DESC);
