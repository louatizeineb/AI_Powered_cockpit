from __future__ import annotations
from app.agent.policy import answer_for_proposal, next_steps_for_action, proposal_for_workflow_result
from app.dqc.resolution.normalizer import normalize_event
from app.dqc.resolution.validator import validate_counts, validate_schema
from app.dqc.resolution.matcher import generate_candidates
from app.dqc.resolution.scoring import confidence
from app.graphrag.retriever import retrieve_catalog_evidence
from app.config import get_settings

settings = get_settings()


def _with_agent_fields(result: dict) -> dict:
    proposal = proposal_for_workflow_result(result)
    response = proposal.as_response(
        mode="deterministic_workflow",
        answer=answer_for_proposal(proposal),
        next_steps=next_steps_for_action(proposal.proposed_action),
    ).model_dump(mode="json")
    return {
        **result,
        **response,
        "human_actions": result.get("human_actions") or next_steps_for_action(proposal.proposed_action),
    }


def run_fixed_workflow(event: dict) -> dict:
    """Supervisor-requested fixed workflow: fuzzy matching -> research -> selection -> explanation -> validation gate."""
    schema = validate_schema(event)
    if not schema.valid:
        return _with_agent_fields(
            {
                "status": "DLQ",
                "requires_human_validation": True,
                "stage": "schema_validation",
                "reason": schema.reason,
                "evidence": {"schema_validation": schema.details or {}},
                "explanation": "The event is missing required DQC fields. Matching is unsafe until the payload is corrected.",
            }
        )

    counts = validate_counts(event)
    if not counts.valid:
        return _with_agent_fields(
            {
                "status": "DLQ",
                "requires_human_validation": True,
                "stage": "business_validation",
                "reason": counts.reason,
                "evidence": {"business_validation": counts.details or {}},
                "explanation": "The event failed deterministic count validation. Matching is unsafe until counts are corrected.",
            }
        )

    normalized = normalize_event(event, source_system="agent_workflow")
    candidates = generate_candidates(normalized, use_embeddings=True, limit=10)
    evidence = retrieve_catalog_evidence(normalized, candidates, top_k=5)

    if not candidates:
        return _with_agent_fields(
            {
                "status": "UNRESOLVED",
                "requires_human_validation": True,
                "stage": "research",
                "normalized": normalized,
                "evidence": evidence,
                "explanation": "No candidates were found after path/token/fuzzy and embedding cosine retrieval. This is likely a missing catalog node, missing app_code, or incompatible DQC naming.",
            }
        )

    best = candidates[0]
    conf = confidence(best.get("match_score", 0), settings.dqc_high_confidence, settings.dqc_medium_confidence)

    if conf == "HIGH":
        recommendation = "AUTO_ATTACH_ALLOWED"
        requires_human = False
        explanation = "The best candidate has high confidence based on path/app_code evidence. It can be attached automatically."
    elif conf == "MEDIUM":
        recommendation = "HUMAN_REVIEW_REQUIRED"
        requires_human = True
        explanation = "The best candidate is plausible but not certain. Human accept/reject is required before final approval."
    else:
        recommendation = "KEEP_IN_DLQ"
        requires_human = True
        explanation = "The best candidate is low confidence. Keep unresolved and inspect catalog/DQC metadata."

    return _with_agent_fields(
        {
            "status": recommendation,
            "requires_human_validation": requires_human,
            "normalized": normalized,
            "best_candidate": best,
            "confidence": conf,
            "evidence": evidence,
            "explanation": explanation,
            "human_actions": ["accept", "reject", "search_alternatives", "replay_after_fix"] if requires_human else [],
        }
    )
