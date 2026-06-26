from __future__ import annotations

from app.migration_v2.routes import _deterministic_governance_answer


def test_deterministic_assistant_explains_selected_issue_and_next_steps():
    answer = _deterministic_governance_answer(
        export_id="export-1",
        message="What should I do with this issue?",
        screen="validation",
        selected_item={
            "issue_id": "issue-123",
            "issue_type": "missing_hierarchy_edge",
            "queue_status": "pending",
            "agent_proposed_policy": "repair",
            "agent_confidence": 0.91,
            "agent_rationale": "Exact edge evidence is missing.",
            "agent_missing_evidence": ["src_node_id/tgt_node_id edge proof"],
        },
        overview_payload={
            "workflow": {"status": "running", "current_phase": "agent-gate-review"},
            "publication": {
                "status": "review_pending",
                "object_counts": {"trusted": 10, "quarantine": 2},
                "relationship_counts": {"trusted": 20},
                "hard_blockers": [],
            },
            "queue_counts": [{"queue_status": "pending", "publish_policy": "repair", "count": 1}],
            "benchmark": {"status": "ready"},
            "publish_report": {"status": "not_run"},
            "search_state": {"active_graph_version": 1, "document_count": 30},
        },
        queue_payload={"total": 1, "items": []},
        activity_payload={"agent_runs": [], "tool_executions": [], "approvals": []},
        schema_payload={"tables": [], "mapping_proposals": []},
    )

    assert "Selected issue `issue-123`" in answer
    assert "current safest policy is `repair`" in answer
    assert "Evidence still needed: src_node_id/tgt_node_id edge proof." in answer
    assert "1. Do not accept it as trusted yet." in answer
    assert "3. Mark repaired only after the deterministic repair evidence is present" in answer


def test_deterministic_assistant_gives_pending_queue_next_steps():
    answer = _deterministic_governance_answer(
        export_id="export-1",
        message="What should I do next before publish?",
        screen="overview",
        selected_item=None,
        overview_payload={
            "workflow": {"status": "running", "current_phase": "validate"},
            "publication": {
                "status": "review_pending",
                "object_counts": {"trusted": 10, "quarantine": 0},
                "relationship_counts": {"trusted": 20},
                "hard_blockers": [],
            },
            "queue_counts": [{"queue_status": "pending", "publish_policy": "needs_human", "count": 3}],
            "benchmark": {"status": "ready"},
            "publish_report": {"status": "ready"},
            "search_state": {"active_graph_version": 1, "document_count": 30},
        },
        queue_payload={"total": 3, "items": []},
        activity_payload={"agent_runs": [], "tool_executions": [], "approvals": []},
        schema_payload={"tables": [], "mapping_proposals": []},
    )

    assert "Open Review issues and clear the pending decision queue." in answer
    assert "Prefer quarantine for bounded uncertainty and repair for structural fixes." in answer
