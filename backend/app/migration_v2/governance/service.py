from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


REPORT_ROOT = Path(__file__).resolve().parents[4] / "reports" / "migration_v2"


def load_report(export_id: str, filename: str) -> dict[str, Any] | None:
    path = REPORT_ROOT / export_id / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_exports(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT export.export_id, export.export_path, export.contract_version,
                   export.status, export.created_at, export.completed_at,
                   workflow.run_id::text, workflow.status AS workflow_status,
                   workflow.current_phase, workflow.updated_at AS workflow_updated_at,
                   snapshot.status AS publication_status, snapshot.object_counts,
                   snapshot.relationship_counts, snapshot.hard_blockers,
                   snapshot.created_at AS snapshot_created_at
            FROM migration_export_run export
            LEFT JOIN LATERAL (
                SELECT * FROM migration_workflow_run run
                WHERE run.export_id = export.export_id
                ORDER BY run.updated_at DESC LIMIT 1
            ) workflow ON true
            LEFT JOIN LATERAL (
                SELECT * FROM migration_publication_snapshot item
                WHERE item.export_id = export.export_id
                ORDER BY item.created_at DESC LIMIT 1
            ) snapshot ON true
            ORDER BY export.created_at DESC
        """)).mappings().all()
    return [dict(row) for row in rows]


def export_overview(engine: Engine, export_id: str) -> dict[str, Any]:
    with engine.connect() as conn:
        export = conn.execute(text("""
            SELECT export_id, export_path, contract_version, status, baseline_status,
                   created_at, started_at, completed_at, metadata
            FROM migration_export_run WHERE export_id = :export_id
        """), {"export_id": export_id}).mappings().first()
        if export is None:
            raise KeyError(export_id)
        workflow = conn.execute(text("""
            SELECT run_id::text, status, current_phase, thread_id, workflow_version,
                   created_by, created_at, started_at, updated_at, completed_at, state
            FROM migration_workflow_run WHERE export_id = :export_id
            ORDER BY updated_at DESC LIMIT 1
        """), {"export_id": export_id}).mappings().first()
        snapshot = conn.execute(text("""
            SELECT id, policy_version, status, object_counts, relationship_counts,
                   hard_blockers, rollback_metadata, evidence, created_by, created_at
            FROM migration_publication_snapshot WHERE export_id = :export_id
            ORDER BY created_at DESC LIMIT 1
        """), {"export_id": export_id}).mappings().first()
        queue_counts = conn.execute(text("""
            SELECT queue_status, publish_policy, count(*) AS count
            FROM migration_validation_queue WHERE export_id = :export_id
            GROUP BY queue_status, publish_policy
        """), {"export_id": export_id}).mappings().all()
        search_state = None
        if conn.execute(text("SELECT to_regclass('public.lineage_search_state')")).scalar():
            row = conn.execute(text("""
                SELECT active_graph_version, document_count, published_at
                FROM lineage_search_state WHERE singleton = true
            """)).mappings().first()
            search_state = dict(row) if row else None
    return {
        "export": dict(export),
        "workflow": dict(workflow) if workflow else None,
        "publication": dict(snapshot) if snapshot else None,
        "queue_counts": [dict(row) for row in queue_counts],
        "search_state": search_state,
        "benchmark": load_report(export_id, "fast_search_benchmark_report.json"),
        "publish_report": load_report(export_id, "publish_report.json"),
    }


def validation_queue(
    engine: Engine,
    export_id: str,
    *,
    status: str | None,
    issue_type: str | None,
    publish_policy: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    filters = ["queue.export_id = :export_id"]
    params: dict[str, Any] = {"export_id": export_id, "limit": limit, "offset": offset}
    if status:
        filters.append("queue.queue_status = :status")
        params["status"] = status
    if issue_type:
        filters.append("queue.issue_type = :issue_type")
        params["issue_type"] = issue_type
    if publish_policy:
        filters.append("queue.publish_policy = :publish_policy")
        params["publish_policy"] = publish_policy
    where = " AND ".join(filters)
    with engine.connect() as conn:
        total = int(conn.execute(text(f"SELECT count(*) FROM migration_validation_queue queue WHERE {where}"), params).scalar_one())
        rows = conn.execute(text(f"""
            SELECT queue.id, queue.issue_id, queue.issue_type, queue.entity_kind,
                   queue.node_id, queue.src_node_id, queue.tgt_node_id, queue.relationship_type,
                   queue.severity, queue.confidence, queue.publish_policy, queue.queue_status,
                   queue.proposed_action, queue.rationale, queue.evidence, queue.approved_by,
                   queue.approved_at, queue.updated_at,
                   proposal.proposed_policy AS agent_proposed_policy,
                   proposal.confidence AS agent_confidence,
                   proposal.rationale AS agent_rationale,
                   proposal.human_question AS agent_question,
                   proposal.missing_evidence AS agent_missing_evidence
            FROM migration_validation_queue queue
            LEFT JOIN LATERAL (
                SELECT * FROM migration_agent_proposal candidate
                WHERE candidate.export_id = queue.export_id AND candidate.issue_id = queue.issue_id
                ORDER BY candidate.created_at DESC LIMIT 1
            ) proposal ON true
            WHERE {where}
            ORDER BY CASE queue.severity WHEN 'ERROR' THEN 0 WHEN 'WARN' THEN 1 ELSE 2 END,
                     queue.updated_at DESC
            LIMIT :limit OFFSET :offset
        """), params).mappings().all()
    return {"total": total, "limit": limit, "offset": offset, "items": [dict(row) for row in rows]}


def decide_queue_issue(
    engine: Engine, export_id: str, issue_id: str, *, decision: str, decided_by: str, rationale: str
) -> dict[str, Any]:
    normalized = decision.strip().lower()
    mapping = {
        "accept": ("accept", "approved"),
        "quarantine": ("quarantine", "approved"),
        "exclude": ("exclude", "approved"),
        "repair": ("repair", "pending"),
        "resolved": ("repair", "resolved"),
        "needs_human": ("needs_human", "pending"),
        "block": ("block", "pending"),
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported decision: {decision}")
    policy, status = mapping[normalized]
    with engine.begin() as conn:
        row = conn.execute(text("""
            UPDATE migration_validation_queue
            SET publish_policy = :policy, queue_status = :status, rationale = :rationale,
                approved_by = CASE WHEN :status IN ('approved', 'resolved') THEN :decided_by ELSE NULL END,
                approved_at = CASE WHEN :status = 'approved' THEN now() ELSE NULL END,
                resolved_at = CASE WHEN :status = 'resolved' THEN now() ELSE NULL END,
                updated_at = now()
            WHERE export_id = :export_id AND issue_id = :issue_id
            RETURNING issue_id, publish_policy, queue_status, approved_by, approved_at, resolved_at
        """), {
            "export_id": export_id, "issue_id": issue_id, "policy": policy, "status": status,
            "rationale": rationale, "decided_by": decided_by,
        }).mappings().first()
        if row is None:
            raise KeyError(issue_id)
        conn.execute(text("""
            UPDATE migration_agent_proposal SET applied_to_queue = true
            WHERE export_id = :export_id AND issue_id = :issue_id AND proposed_policy = :policy
        """), {"export_id": export_id, "issue_id": issue_id, "policy": policy})
    return {**dict(row), "projection_refresh_required": True}


def activity(engine: Engine, export_id: str, limit: int = 100) -> dict[str, Any]:
    with engine.connect() as conn:
        agents = conn.execute(text("""
            SELECT id, workflow_run_id::text, agent_name, mode, model_name, status,
                   reviewed_count, proposal_count, llm_call_count, fallback_count,
                   errors, started_at, completed_at
            FROM migration_agent_run WHERE export_id = :export_id
            ORDER BY started_at DESC LIMIT :limit
        """), {"export_id": export_id, "limit": limit}).mappings().all()
        tools = conn.execute(text("""
            SELECT execution.execution_id::text, execution.run_id::text, execution.tool_name,
                   execution.tool_version, execution.agent_name, execution.status,
                   execution.input_hash, execution.generated_artifacts, execution.database_effects,
                   execution.error, execution.started_at, execution.completed_at
            FROM migration_tool_execution execution
            JOIN migration_workflow_run workflow ON workflow.run_id = execution.run_id
            WHERE workflow.export_id = :export_id
            ORDER BY execution.created_at DESC LIMIT :limit
        """), {"export_id": export_id, "limit": limit}).mappings().all()
        approvals = conn.execute(text("""
            SELECT approval.approval_id::text, approval.run_id::text, approval.gate_name,
                   approval.status, approval.question, approval.evidence, approval.decision,
                   approval.rationale, approval.requested_by, approval.decided_by,
                   approval.requested_at, approval.decided_at
            FROM migration_approval_request approval
            JOIN migration_workflow_run workflow ON workflow.run_id = approval.run_id
            WHERE workflow.export_id = :export_id
            ORDER BY approval.requested_at DESC LIMIT :limit
        """), {"export_id": export_id, "limit": limit}).mappings().all()
    return {"agent_runs": [dict(row) for row in agents], "tool_executions": [dict(row) for row in tools], "approvals": [dict(row) for row in approvals]}


def agent_evaluations(engine: Engine, export_id: str, limit: int = 100) -> dict[str, Any]:
    with engine.connect() as conn:
        if not conn.execute(text("SELECT to_regclass('public.migration_agent_eval_run')")).scalar():
            return {"latest_run": None, "runs": [], "scores": [], "case_count": 0}
        latest = conn.execute(text("""
            SELECT id, export_id, agent_name, eval_name, mode, model_name, status,
                   case_count, policy_accuracy, unsafe_accept_count, blocker_recall,
                   valid_policy_rate, question_present_rate, rationale_present_rate,
                   average_confidence, latency_ms, langsmith_project, langsmith_trace_url,
                   summary, errors, started_at, completed_at
            FROM migration_agent_eval_run
            WHERE export_id = :export_id
            ORDER BY started_at DESC LIMIT 1
        """), {"export_id": export_id}).mappings().first()
        runs = conn.execute(text("""
            SELECT id, agent_name, eval_name, mode, status, case_count,
                   policy_accuracy, unsafe_accept_count, blocker_recall,
                   valid_policy_rate, question_present_rate, average_confidence,
                   latency_ms, started_at, completed_at
            FROM migration_agent_eval_run
            WHERE export_id = :export_id
            ORDER BY started_at DESC LIMIT :limit
        """), {"export_id": export_id, "limit": limit}).mappings().all()
        scores = []
        if latest:
            scores = conn.execute(text("""
                SELECT id, eval_run_id, case_id, issue_id, issue_type,
                       expected_policy, proposed_policy, confidence, policy_exact,
                       unsafe_accept, blocker_expected, blocker_recalled, valid_policy,
                       rationale_present, question_present, evidence_grounded_score,
                       source, rationale, human_question, missing_evidence,
                       guardrail_actions, details, created_at
                FROM migration_agent_eval_score
                WHERE eval_run_id = :eval_run_id
                ORDER BY unsafe_accept DESC, policy_exact ASC, blocker_expected DESC,
                         evidence_grounded_score ASC, confidence DESC
                LIMIT :limit
            """), {"eval_run_id": latest["id"], "limit": limit}).mappings().all()
        case_count = int(conn.execute(text("""
            SELECT count(*) FROM migration_agent_eval_case
            WHERE export_id = :export_id AND active = true
        """), {"export_id": export_id}).scalar_one())
    return {
        "latest_run": dict(latest) if latest else None,
        "runs": [dict(row) for row in runs],
        "scores": [dict(row) for row in scores],
        "case_count": case_count,
    }


def schema_summary(engine: Engine, export_id: str) -> dict[str, Any]:
    with engine.connect() as conn:
        tables = conn.execute(text("""
            SELECT raw.raw_table_name, max(raw.row_count) AS row_count,
                   count(profile.id) AS column_count,
                   count(*) FILTER (WHERE profile.warnings <> '[]'::jsonb) AS warning_count
            FROM migration_raw_file raw
            LEFT JOIN migration_column_profile profile
              ON profile.export_id = raw.export_id AND profile.raw_table_name = raw.raw_table_name
            WHERE raw.export_id = :export_id
            GROUP BY raw.raw_table_name ORDER BY raw.raw_table_name
        """), {"export_id": export_id}).mappings().all()
        proposals = conn.execute(text("""
            SELECT id, workflow_run_id::text, raw_table_name, raw_column_name,
                   current_canonical_field, proposed_canonical_field, proposed_action,
                   confidence, rationale, missing_evidence, human_question, candidate_columns,
                   status, approved_by, approved_at, created_at
            FROM migration_schema_mapping_proposal WHERE export_id = :export_id
            ORDER BY created_at DESC
        """), {"export_id": export_id}).mappings().all()
    return {"tables": [dict(row) for row in tables], "mapping_proposals": [dict(row) for row in proposals]}


def provenance(engine: Engine, export_id: str, *, subject: str | None, limit: int) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT event_id, export_id, event_type, actor, status, occurred_at, subject_id, payload
            FROM migration_governance_provenance
            WHERE export_id = :export_id
              AND (:subject IS NULL OR subject_id = :subject OR payload::text ILIKE '%' || :subject || '%')
            ORDER BY occurred_at DESC LIMIT :limit
        """), {"export_id": export_id, "subject": subject, "limit": limit}).mappings().all()
    return [dict(row) for row in rows]
