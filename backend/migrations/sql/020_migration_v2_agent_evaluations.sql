CREATE TABLE IF NOT EXISTS migration_agent_eval_case (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL,
    case_id text NOT NULL,
    issue_id text NOT NULL,
    issue_type text,
    expected_policy text NOT NULL,
    expected_status text,
    severity text,
    evidence_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    source text NOT NULL DEFAULT 'validation_queue',
    notes text NOT NULL DEFAULT '',
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (export_id, case_id)
);

CREATE TABLE IF NOT EXISTS migration_agent_eval_run (
    id bigserial PRIMARY KEY,
    export_id text NOT NULL,
    agent_name text NOT NULL,
    eval_name text NOT NULL,
    mode text NOT NULL,
    model_name text,
    status text NOT NULL DEFAULT 'running',
    case_count integer NOT NULL DEFAULT 0,
    policy_accuracy double precision NOT NULL DEFAULT 0,
    unsafe_accept_count integer NOT NULL DEFAULT 0,
    blocker_recall double precision NOT NULL DEFAULT 0,
    valid_policy_rate double precision NOT NULL DEFAULT 0,
    question_present_rate double precision NOT NULL DEFAULT 0,
    rationale_present_rate double precision NOT NULL DEFAULT 0,
    average_confidence double precision NOT NULL DEFAULT 0,
    latency_ms double precision NOT NULL DEFAULT 0,
    langsmith_project text,
    langsmith_trace_url text,
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    errors jsonb NOT NULL DEFAULT '[]'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS migration_agent_eval_score (
    id bigserial PRIMARY KEY,
    eval_run_id bigint NOT NULL REFERENCES migration_agent_eval_run(id) ON DELETE CASCADE,
    export_id text NOT NULL,
    case_id text NOT NULL,
    issue_id text NOT NULL,
    issue_type text,
    expected_policy text NOT NULL,
    proposed_policy text NOT NULL,
    confidence double precision NOT NULL DEFAULT 0,
    policy_exact boolean NOT NULL DEFAULT false,
    unsafe_accept boolean NOT NULL DEFAULT false,
    blocker_expected boolean NOT NULL DEFAULT false,
    blocker_recalled boolean NOT NULL DEFAULT false,
    valid_policy boolean NOT NULL DEFAULT false,
    rationale_present boolean NOT NULL DEFAULT false,
    question_present boolean NOT NULL DEFAULT false,
    evidence_grounded_score double precision NOT NULL DEFAULT 0,
    latency_ms double precision NOT NULL DEFAULT 0,
    rationale text NOT NULL DEFAULT '',
    human_question text NOT NULL DEFAULT '',
    missing_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    guardrail_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
    source text NOT NULL DEFAULT '',
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_migration_agent_eval_case_export
    ON migration_agent_eval_case(export_id, active, issue_type);

CREATE INDEX IF NOT EXISTS idx_migration_agent_eval_run_export
    ON migration_agent_eval_run(export_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_migration_agent_eval_score_run
    ON migration_agent_eval_score(eval_run_id, policy_exact, unsafe_accept);

ALTER TABLE migration_agent_eval_run
    ADD COLUMN IF NOT EXISTS average_evidence_plan_score double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS average_query_intent_score double precision NOT NULL DEFAULT 0;

ALTER TABLE migration_agent_eval_score
    ADD COLUMN IF NOT EXISTS evidence_plan_present boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS evidence_plan_score double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS query_intent_score double precision NOT NULL DEFAULT 0;
