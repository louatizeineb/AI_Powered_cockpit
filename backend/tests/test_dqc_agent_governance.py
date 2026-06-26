from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.contracts import DQC_AGENT_MANIFEST, DQCAgentProposal
from app.agent.policy import (
    enforce_proposal_guardrails,
    proposal_for_dlq_item,
    proposal_for_resolved_item,
)
from app.agent.routes import AgentChatRequest, simple_agent_chat
from app.agent.tools import validate_tool_input


def test_dqc_agent_manifest_does_not_allow_review_mutations():
    assert "approve_match" not in DQC_AGENT_MANIFEST.allowed_tools
    assert "reject_match" not in DQC_AGENT_MANIFEST.allowed_tools
    assert "approve_match" in DQC_AGENT_MANIFEST.requires_approval_for
    assert "reject_match" in DQC_AGENT_MANIFEST.requires_approval_for


@pytest.mark.parametrize(
    ("reason", "stage", "expected"),
    [
        ("MISSING_DQC_CRITICAL_DATA", "SCHEMA_VALIDATION", "replay_after_fix"),
        ("COUNT_INCONSISTENCY", "BUSINESS_VALIDATION", "replay_after_fix"),
        ("NO_CATALOG_CANDIDATE", "MATCHING", "keep_in_dlq"),
        ("LOW_CONFIDENCE_MATCH", "MATCHING", "search_alternatives"),
    ],
)
def test_dqc_dlq_deterministic_proposals(reason, stage, expected):
    proposal = proposal_for_dlq_item(
        {
            "id": 10,
            "failure_reason": reason,
            "failure_stage": stage,
            "failure_details": {"example": True},
        }
    )
    assert proposal.proposed_action == expected
    assert proposal.human_question
    assert proposal.evidence["dlq"]["id"] == 10


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"id": 1, "resolution_status": "MATCHED", "confidence_level": "HIGH", "match_score": 95}, "approve_match"),
        ({"id": 2, "resolution_status": "MATCHED_WITH_REVIEW", "confidence_level": "MEDIUM", "human_review_required": True}, "search_alternatives"),
        ({"id": 3, "resolution_status": "MATCH_REJECTED", "confidence_level": "MEDIUM"}, "search_alternatives"),
        ({"id": 4, "resolution_status": "MATCHED", "confidence_level": "LOW", "match_score": 30}, "reject_match"),
    ],
)
def test_dqc_resolved_deterministic_proposals(item, expected):
    proposal = proposal_for_resolved_item(item)
    assert proposal.proposed_action == expected
    assert proposal.subject_type == "resolved"


def test_dqc_low_confidence_approval_is_guarded():
    proposal = DQCAgentProposal(
        subject_type="resolved",
        subject_id=1,
        proposed_action="approve_match",
        confidence="LOW",
        rationale="bad idea",
    )
    guarded = enforce_proposal_guardrails(proposal)
    assert guarded.proposed_action == "search_alternatives"
    assert guarded.guardrail_actions


def test_dqc_tool_registry_rejects_review_mutations_and_unknown_fields():
    with pytest.raises(PermissionError):
        validate_tool_input("approve_match", {"resolved_id": 1, "reviewer": "agent"})
    with pytest.raises(ValidationError):
        validate_tool_input("list_unresolved", {"limit": 10, "sql": "DROP TABLE dqc_resolved"})


def test_dqc_chat_selected_dlq_returns_next_steps_without_applying_decision():
    response = simple_agent_chat(
        AgentChatRequest(
            message="What should I do with this issue?",
            selected_item={
                "id": 42,
                "failure_stage": "BUSINESS_VALIDATION",
                "failure_reason": "COUNT_INCONSISTENCY",
                "failure_details": {"controlleditemcount": 10, "expected_total": 8},
            },
        )
    )
    assert response["recommended_action"] == "replay_after_fix"
    assert response["proposal_id"] is None
    assert "review_endpoints_apply_decisions" not in response.get("guardrails", [])
    assert response["next_steps"]
