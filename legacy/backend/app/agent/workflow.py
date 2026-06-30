from __future__ import annotations
from app.dqc.resolution.normalizer import normalize_event
from app.dqc.resolution.matcher import generate_candidates
from app.dqc.resolution.scoring import confidence
from app.graphrag.retriever import retrieve_catalog_evidence
from app.config import get_settings

settings = get_settings()


def run_fixed_workflow(event: dict) -> dict:
    """Supervisor-requested fixed workflow: fuzzy matching -> research -> selection -> explanation -> validation gate."""
    normalized = normalize_event(event, source_system="agent_workflow")
    candidates = generate_candidates(normalized, use_embeddings=True, limit=10)
    evidence = retrieve_catalog_evidence(normalized, candidates, top_k=5)

    if not candidates:
        return {
            "status": "UNRESOLVED",
            "requires_human_validation": True,
            "stage": "research",
            "normalized": normalized,
            "evidence": evidence,
            "explanation": "No candidates were found after path/token/fuzzy and embedding cosine retrieval. This is likely a missing catalog node, missing app_code, or incompatible DQC naming.",
        }

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

    return {
        "status": recommendation,
        "requires_human_validation": requires_human,
        "normalized": normalized,
        "best_candidate": best,
        "confidence": conf,
        "evidence": evidence,
        "explanation": explanation,
        "human_actions": ["accept", "reject", "search_alternatives", "replay_after_fix"] if requires_human else [],
    }
