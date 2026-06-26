CREATE TABLE IF NOT EXISTS migration_workflow_run (
    run_id uuid PRIMARY KEY,
    export_id text NOT NULL REFERENCES migration_export_run(export_id) ON DELETE CASCADE,
    workflow_name text NOT NULL DEFAULT 'migration_v2',
    workflow_version text NOT NULL,
    contract_version text,
    export_fingerprint text NOT NULL,
    idempotency_key text NOT NULL,
    thread_id text NOT NULL,
    trigger_type text NOT NULL,
    trigger_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'received',
    current_phase text NOT NULL DEFAULT 'received',
    state jsonb NOT NULL DEFAULT '{}'::jsonb,
    configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
    error jsonb,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (idempotency_key),
    UNIQUE (thread_id),
    CHECK (status IN ('received', 'running', 'waiting_approval', 'blocked', 'failed', 'ready', 'published', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS migration_workflow_transition (
    id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES migration_workflow_run(run_id) ON DELETE CASCADE,
    from_status text,
    to_status text NOT NULL,
    from_phase text,
    to_phase text NOT NULL,
    actor_type text NOT NULL,
    actor_name text NOT NULL,
    reason text,
    state_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS migration_workflow_checkpoint (
    id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES migration_workflow_run(run_id) ON DELETE CASCADE,
    thread_id text NOT NULL,
    checkpoint_namespace text NOT NULL DEFAULT 'migration_v2',
    checkpoint_id text NOT NULL,
    phase text NOT NULL,
    state jsonb NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (thread_id, checkpoint_namespace, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS migration_approval_request (
    approval_id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES migration_workflow_run(run_id) ON DELETE CASCADE,
    gate_name text NOT NULL,
    requested_by text NOT NULL,
    required_role text,
    status text NOT NULL DEFAULT 'pending',
    question text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    decision text,
    rationale text,
    decided_by text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled'))
);

-- Earlier Phase 1 development builds used a broad uniqueness constraint here.
-- Keep migration replay idempotent while allowing a gate to be approved again
-- after new evidence arrives.
ALTER TABLE migration_approval_request
    DROP CONSTRAINT IF EXISTS migration_approval_request_run_id_gate_name_status_key;

CREATE TABLE IF NOT EXISTS migration_tool_execution (
    execution_id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES migration_workflow_run(run_id) ON DELETE CASCADE,
    tool_name text NOT NULL,
    tool_version text NOT NULL,
    agent_name text,
    idempotency_key text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    input_hash text NOT NULL,
    input_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    output_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    generated_artifacts jsonb NOT NULL DEFAULT '[]'::jsonb,
    error jsonb,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, idempotency_key),
    CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_migration_workflow_run_export
    ON migration_workflow_run(export_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_migration_workflow_run_status
    ON migration_workflow_run(status, current_phase, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_migration_workflow_transition_run
    ON migration_workflow_transition(run_id, created_at);

CREATE INDEX IF NOT EXISTS idx_migration_workflow_checkpoint_run
    ON migration_workflow_checkpoint(run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_migration_approval_request_pending
    ON migration_approval_request(run_id, status, requested_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_migration_approval_request_pending_gate
    ON migration_approval_request(run_id, gate_name)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_migration_tool_execution_run
    ON migration_tool_execution(run_id, status, created_at);
