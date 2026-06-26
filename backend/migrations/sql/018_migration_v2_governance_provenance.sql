CREATE OR REPLACE VIEW migration_governance_provenance AS
SELECT
    'queue:' || queue.id::text AS event_id,
    queue.export_id,
    'validation_decision'::text AS event_type,
    coalesce(queue.approved_by, queue.proposed_by) AS actor,
    queue.queue_status AS status,
    coalesce(queue.approved_at, queue.resolved_at, queue.updated_at, queue.created_at) AS occurred_at,
    queue.issue_id AS subject_id,
    jsonb_build_object(
        'issue_type', queue.issue_type,
        'entity_kind', queue.entity_kind,
        'publish_policy', queue.publish_policy,
        'severity', queue.severity,
        'rationale', queue.rationale,
        'evidence', queue.evidence
    ) AS payload
FROM migration_validation_queue queue
UNION ALL
SELECT
    'agent:' || run.id::text,
    run.export_id,
    'agent_run',
    run.agent_name,
    run.status,
    coalesce(run.completed_at, run.started_at),
    run.agent_name || ':' || run.id::text,
    jsonb_build_object(
        'mode', run.mode,
        'model_name', run.model_name,
        'reviewed_count', run.reviewed_count,
        'proposal_count', run.proposal_count,
        'llm_call_count', run.llm_call_count,
        'fallback_count', run.fallback_count,
        'errors', run.errors
    )
FROM migration_agent_run run
UNION ALL
SELECT
    'tool:' || execution.execution_id::text,
    workflow.export_id,
    'tool_execution',
    coalesce(execution.agent_name, 'orchestrator'),
    execution.status,
    coalesce(execution.completed_at, execution.started_at, execution.created_at),
    execution.tool_name || ':' || execution.execution_id::text,
    jsonb_build_object(
        'tool_name', execution.tool_name,
        'tool_version', execution.tool_version,
        'input_hash', execution.input_hash,
        'generated_artifacts', execution.generated_artifacts,
        'database_effects', execution.database_effects,
        'error', execution.error
    )
FROM migration_tool_execution execution
JOIN migration_workflow_run workflow ON workflow.run_id = execution.run_id
UNION ALL
SELECT
    'approval:' || approval.approval_id::text,
    workflow.export_id,
    'approval',
    coalesce(approval.decided_by, approval.requested_by),
    approval.status,
    coalesce(approval.decided_at, approval.requested_at),
    approval.gate_name || ':' || approval.approval_id::text,
    jsonb_build_object(
        'gate_name', approval.gate_name,
        'question', approval.question,
        'decision', approval.decision,
        'rationale', approval.rationale,
        'evidence', approval.evidence
    )
FROM migration_approval_request approval
JOIN migration_workflow_run workflow ON workflow.run_id = approval.run_id
UNION ALL
SELECT
    'publish:' || snapshot.id::text,
    snapshot.export_id,
    'publication_snapshot',
    snapshot.created_by,
    snapshot.status,
    snapshot.created_at,
    'publication:' || snapshot.id::text,
    jsonb_build_object(
        'policy_version', snapshot.policy_version,
        'object_counts', snapshot.object_counts,
        'relationship_counts', snapshot.relationship_counts,
        'hard_blockers', snapshot.hard_blockers,
        'rollback_metadata', snapshot.rollback_metadata,
        'evidence', snapshot.evidence
    )
FROM migration_publication_snapshot snapshot;

COMMENT ON VIEW migration_governance_provenance IS
    'Read-only provenance projection used by governance APIs and GraphRAG explanations.';
