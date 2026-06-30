from __future__ import annotations

from typing import Any

from app.agent.contracts import DQCAgentProposal


ALLOWED_DQC_ACTIONS = {
    "approve_match",
    "reject_match",
    "keep_in_dlq",
    "search_alternatives",
    "replay_after_fix",
}


def _score(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _citation(subject_type: str, subject_id: int | None, label: str) -> dict[str, Any]:
    return {"type": subject_type, "subject": subject_id, "label": label}


def next_steps_for_action(action: str) -> list[str]:
    return {
        "approve_match": [
            "Confirm the candidate path and entity level match the DQC controlled object.",
            "Use the review approve endpoint or UI action with a reviewer note.",
            "Refresh resolved items and keep the proposal as audit evidence.",
        ],
        "reject_match": [
            "Reject the proposed match with the reason it is unsafe.",
            "Search alternatives or repair the catalog/DQC naming source.",
            "Replay the event only after the source issue is fixed.",
        ],
        "keep_in_dlq": [
            "Keep the event unresolved.",
            "Inspect the missing fields, count evidence, or catalog export gap.",
            "Replay only after the source data or catalog evidence changes.",
        ],
        "search_alternatives": [
            "Inspect the top candidates and GraphRAG evidence.",
            "Search by app code, structure, field, and full path.",
            "Approve only if a reviewer can cite the exact catalog node.",
        ],
        "replay_after_fix": [
            "Fix the schema/count/catalog issue outside chat.",
            "Replay the event through the DQC processing endpoint.",
            "Review the new proposal before applying a match.",
        ],
    }.get(action, ["Review the evidence, then choose an explicit reviewer action."])


def proposal_for_workflow_result(result: dict[str, Any]) -> DQCAgentProposal:
    status = str(result.get("status") or "")
    normalized = result.get("normalized") or {}
    best = result.get("best_candidate") or result.get("best") or {}
    evidence = {
        "normalized": normalized,
        "best_candidate": best,
        "retrieved_evidence": result.get("evidence") or {},
        "status": status,
    }
    if status in {"AUTO_ATTACH_ALLOWED", "MATCHED"}:
        action = "approve_match"
        confidence = result.get("confidence") or "HIGH"
        rationale = "The best candidate is high confidence and can be approved if reviewer evidence matches the controlled object."
        missing = []
        question = "Do you approve this high-confidence catalog match?"
    elif status in {"HUMAN_REVIEW_REQUIRED", "MATCHED_WITH_REVIEW"}:
        action = "search_alternatives"
        confidence = result.get("confidence") or "MEDIUM"
        rationale = "The candidate is plausible but must remain a proposal until a reviewer accepts or rejects it."
        missing = ["reviewer confirmation of candidate path and entity level"]
        question = "Should this medium-confidence match be accepted, rejected, or compared with alternatives?"
    elif status in {"KEEP_IN_DLQ", "UNRESOLVED"}:
        action = "keep_in_dlq"
        confidence = result.get("confidence") or "LOW"
        rationale = result.get("explanation") or "The workflow did not produce safe match evidence."
        missing = ["strong catalog candidate evidence"]
        question = "What source or catalog evidence should be fixed before replay?"
    else:
        action = "keep_in_dlq"
        confidence = result.get("confidence")
        rationale = result.get("explanation") or "The workflow outcome needs human review."
        missing = ["manual classification"]
        question = "Which reviewer action should be assigned to this result?"
    return enforce_proposal_guardrails(
        DQCAgentProposal(
            subject_type="workflow_result",
            subject_id=result.get("resolved_id") or result.get("dlq_id"),
            proposed_action=action,
            confidence=confidence,
            rationale=rationale,
            missing_evidence=missing,
            human_question=question,
            evidence=evidence,
        )
    )


def proposal_for_resolved_item(item: dict[str, Any]) -> DQCAgentProposal:
    review_required = bool(item.get("human_review_required"))
    status = str(item.get("resolution_status") or "")
    score = _score(item.get("match_score"))
    confidence = item.get("confidence_level") or ("HIGH" if score >= 85 else "MEDIUM" if score >= 65 else "LOW")
    evidence = {"resolved": item}
    if status == "MATCH_REJECTED":
        action = "search_alternatives"
        rationale = "This match has already been rejected; search alternatives or repair the source/candidate evidence."
        missing = ["alternative candidate evidence"]
        question = "Which alternative catalog node should be investigated?"
    elif review_required or confidence == "MEDIUM":
        action = "search_alternatives"
        rationale = "This match requires reviewer confirmation before it can become approved evidence."
        missing = ["reviewer approval or rejection"]
        question = "Should the reviewer accept this proposed match or reject it?"
    elif confidence == "HIGH":
        action = "approve_match"
        rationale = "This is a high-confidence match; approval still belongs to the reviewer endpoint or UI."
        missing = []
        question = "Do you approve this high-confidence match?"
    else:
        action = "reject_match"
        rationale = "This low-confidence resolved item should not be trusted without stronger evidence."
        missing = ["stronger candidate evidence"]
        question = "Should this low-confidence item be rejected and searched again?"
    return enforce_proposal_guardrails(
        DQCAgentProposal(
            subject_type="resolved",
            subject_id=item.get("id"),
            proposed_action=action,
            confidence=confidence,
            rationale=rationale,
            missing_evidence=missing,
            human_question=question,
            evidence=evidence,
        )
    )


def proposal_for_dlq_item(item: dict[str, Any]) -> DQCAgentProposal:
    reason = str(item.get("failure_reason") or "")
    stage = str(item.get("failure_stage") or "")
    details = item.get("failure_details") or {}
    if reason in {"MISSING_DQC_CRITICAL_DATA", "INVALID_COUNT_FIELDS", "COUNT_INCONSISTENCY"} or stage in {
        "SCHEMA_VALIDATION",
        "BUSINESS_VALIDATION",
    }:
        action = "replay_after_fix"
        confidence = "HIGH"
        rationale = "The event failed deterministic DQC validation; the source data must be fixed before matching is safe."
        missing = ["corrected DQC payload"]
        question = "Can the DQC source owner fix the invalid payload and replay it?"
    elif reason == "NO_CATALOG_CANDIDATE":
        action = "keep_in_dlq"
        confidence = "HIGH"
        rationale = "No catalog candidate was found, so approving a match would invent a catalog node."
        missing = ["catalog export evidence for the controlled object"]
        question = "Is the catalog missing this object, or is the DQC naming incompatible?"
    elif reason == "LOW_CONFIDENCE_MATCH":
        action = "search_alternatives"
        confidence = "MEDIUM"
        rationale = "The best match was too weak for approval, but alternatives may exist."
        missing = ["review of top candidates and catalog path evidence"]
        question = "Which alternative candidate should be investigated?"
    else:
        action = "keep_in_dlq"
        confidence = "MEDIUM"
        rationale = "The unresolved event needs human classification before replay or approval."
        missing = ["manual investigation"]
        question = "What source or catalog issue explains this unresolved event?"
    return enforce_proposal_guardrails(
        DQCAgentProposal(
            subject_type="dlq",
            subject_id=item.get("id"),
            proposed_action=action,
            confidence=confidence,
            rationale=rationale,
            missing_evidence=missing,
            human_question=question,
            evidence={"dlq": item, "details": details},
        )
    )


def enforce_proposal_guardrails(proposal: DQCAgentProposal) -> DQCAgentProposal:
    if proposal.proposed_action not in ALLOWED_DQC_ACTIONS:
        proposal.guardrail_actions.append(f"Invalid action `{proposal.proposed_action}` downgraded to keep_in_dlq.")
        proposal.proposed_action = "keep_in_dlq"
    if proposal.proposed_action in {"approve_match", "reject_match"}:
        proposal.guardrail_actions.append("Agent may propose this action only; reviewer endpoint/UI must apply it.")
    if proposal.proposed_action == "approve_match" and str(proposal.confidence).upper() == "LOW":
        proposal.guardrail_actions.append("Low-confidence approval downgraded to search_alternatives.")
        proposal.proposed_action = "search_alternatives"
    return proposal


def answer_for_proposal(proposal: DQCAgentProposal) -> str:
    return "\n\n".join(
        [
            proposal.rationale,
            f"Recommended action: `{proposal.proposed_action}`.",
            proposal.human_question,
        ]
    )


def citations_for_proposal(proposal: DQCAgentProposal) -> list[dict[str, Any]]:
    return [_citation(proposal.subject_type, proposal.subject_id, proposal.proposed_action)]
