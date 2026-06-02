from __future__ import annotations

def explain_unresolved(normalized: dict | None, candidates: list[dict], reason: str) -> str:
    """Safe deterministic explanation. The agent can later enrich this with LLM wording."""
    if reason == "NO_CATALOG_CANDIDATE":
        return "No catalog candidate was found using app_code, path tokens, fuzzy matching, or embedding fallback. This may indicate a missing catalog export, nonexistent node, or incompatible DQC naming."
    if reason == "LOW_CONFIDENCE_MATCH":
        best = candidates[0] if candidates else None
        if best:
            return f"The best candidate scored only {best.get('match_score')}. The path was {best.get('raw_path_full')}. Human review or catalog correction is required."
        return "Candidate generation returned weak matches only. Human investigation is required."
    return f"DQC event unresolved: {reason}."
