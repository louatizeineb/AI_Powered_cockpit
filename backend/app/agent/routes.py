from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from typing import Any
from app.agent.contracts import DQC_AGENT_MANIFEST
from app.agent.persistence import finish_agent_run, save_agent_proposal, start_agent_run
from app.agent.policy import (
    answer_for_proposal,
    citations_for_proposal,
    next_steps_for_action,
    proposal_for_dlq_item,
    proposal_for_resolved_item,
    proposal_for_workflow_result,
)
from app.agent.workflow import run_fixed_workflow
from app.agent.azure_llm import explain_with_llm
from app.agent.tools import tool_list_unresolved, tool_list_resolved

router = APIRouter(prefix="/agent/dqc", tags=["DQC Agent"])


class AgentWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: dict
    use_llm_explanation: bool = False


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    limit: int = Field(default=10, ge=1, le=100)
    selected_item: dict[str, Any] | None = None
    use_llm_explanation: bool = False


def _persist_response(mode: str, message: str, proposal, response, reviewed_count: int = 1) -> dict:
    run_id = start_agent_run(mode=mode, message=message, source="chat")
    proposal_id = save_agent_proposal(run_id, proposal)
    finish_agent_run(
        run_id,
        status="completed",
        reviewed_count=reviewed_count,
        proposal_count=1,
        fallback_count=1 if "deterministic" in mode else 0,
    )
    payload = response.model_dump(mode="json")
    payload["proposal_id"] = proposal_id
    payload["manifest"] = DQC_AGENT_MANIFEST.as_dict()
    return payload


def _response_from_proposal(proposal, *, mode: str):
    return proposal.as_response(
        mode=mode,
        answer=answer_for_proposal(proposal),
        next_steps=next_steps_for_action(proposal.proposed_action),
        citations=citations_for_proposal(proposal),
    )


def _first_item(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return items[0] if items else None


@router.post("/run-workflow")
def run_workflow(payload: AgentWorkflowRequest):
    result = run_fixed_workflow(payload.event)
    proposal = proposal_for_workflow_result(result)
    run_id = start_agent_run(mode="deterministic_workflow", message="run-workflow", source="workflow")
    proposal_id = save_agent_proposal(run_id, proposal)
    finish_agent_run(
        run_id,
        status="completed",
        reviewed_count=1,
        proposal_count=1,
        fallback_count=1,
    )
    result["proposal_id"] = proposal_id
    result["manifest"] = DQC_AGENT_MANIFEST.as_dict()
    if payload.use_llm_explanation:
        result["llm_explanation"] = explain_with_llm("Explain DQC matching workflow result", result)
    return result


@router.post("/chat")
def simple_agent_chat(payload: AgentChatRequest):
    """Controlled DQC agent endpoint: read evidence, propose next steps, never apply review decisions."""
    msg = payload.message.lower()
    selected = payload.selected_item or {}
    mode = "deterministic_chat"

    if selected.get("failure_reason") or selected.get("failure_stage"):
        proposal = proposal_for_dlq_item(selected)
        response = _response_from_proposal(proposal, mode=mode)
        return _persist_response(mode, payload.message, proposal, response)

    if selected.get("resolution_status") or selected.get("matched_node_id"):
        proposal = proposal_for_resolved_item(selected)
        response = _response_from_proposal(proposal, mode=mode)
        return _persist_response(mode, payload.message, proposal, response)

    if "unresolved" in msg or "dlq" in msg or "blocked" in msg:
        data = tool_list_unresolved(payload.limit)
        item = _first_item(data["items"])
        if item:
            proposal = proposal_for_dlq_item(item)
            response = _response_from_proposal(proposal, mode=mode)
            payload_out = _persist_response(mode, payload.message, proposal, response, reviewed_count=len(data["items"]))
            payload_out["tool_used"] = "list_unresolved"
            payload_out["data"] = data
            return payload_out
        return {
            "status": "ok",
            "mode": mode,
            "answer": "No unresolved DQC events were returned by the current evidence query.",
            "recommended_action": "none",
            "confidence": "HIGH",
            "evidence": data,
            "missing_evidence": [],
            "next_steps": ["Refresh DQC data or process a new event before asking for unresolved guidance."],
            "guardrails": ["read_only_chat", "no_review_decisions_from_chat"],
            "citations": [],
            "proposal_id": None,
            "manifest": DQC_AGENT_MANIFEST.as_dict(),
        }

    if "resolved" in msg or "matched" in msg or "approve" in msg or "reject" in msg:
        data = tool_list_resolved(payload.limit)
        item = _first_item(data["items"])
        if item:
            proposal = proposal_for_resolved_item(item)
            response = _response_from_proposal(proposal, mode=mode)
            payload_out = _persist_response(mode, payload.message, proposal, response, reviewed_count=len(data["items"]))
            payload_out["tool_used"] = "list_resolved"
            payload_out["data"] = data
            return payload_out
        return {
            "status": "ok",
            "mode": mode,
            "answer": "No resolved DQC matches were returned by the current evidence query.",
            "recommended_action": "none",
            "confidence": "HIGH",
            "evidence": data,
            "missing_evidence": [],
            "next_steps": ["Process DQC events, then review proposed or confirmed matches."],
            "guardrails": ["read_only_chat", "no_review_decisions_from_chat"],
            "citations": [],
            "proposal_id": None,
            "manifest": DQC_AGENT_MANIFEST.as_dict(),
        }

    answer = (
        "The DQC agent can inspect unresolved DLQ events, explain resolved matches, and propose reviewer actions. "
        "It cannot approve, reject, or replay from chat."
    )
    if payload.use_llm_explanation:
        answer = explain_with_llm(payload.message, {"guardrails": DQC_AGENT_MANIFEST.as_dict()})
    return {
        "status": "ok",
        "mode": mode,
        "answer": answer,
        "recommended_action": "ask_about_unresolved_or_resolved",
        "confidence": "HIGH",
        "evidence": {"manifest": DQC_AGENT_MANIFEST.as_dict()},
        "missing_evidence": ["specific unresolved or resolved item"],
        "next_steps": [
            "Ask about unresolved/DLQ events to triage failures.",
            "Ask about resolved/matched events to review candidate approvals.",
            "Use /agent/dqc/run-workflow with a DQC event for event-specific evidence.",
        ],
        "guardrails": ["read_only_chat", "no_review_decisions_from_chat", "review_endpoints_apply_decisions"],
        "citations": [],
        "proposal_id": None,
        "manifest": DQC_AGENT_MANIFEST.as_dict(),
    }
