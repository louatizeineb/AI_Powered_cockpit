from __future__ import annotations
from rapidfuzz import fuzz


def score_candidate(normalized: dict, candidate: dict) -> dict:
    score = 0.0
    reasons: list[str] = []
    method = "PATH_TOKEN"

    app = normalized.get("application_code_norm")
    structure = normalized.get("controlled_structure_name")
    field = normalized.get("controlled_field_name")
    source = normalized.get("controlled_source_name_norm")

    candidate_app = candidate.get("app_code_from_path")
    leaf = candidate.get("leaf_name")
    parent = candidate.get("parent_name")
    normalized_path = candidate.get("normalized_path") or ""
    tokens = set(candidate.get("path_tokens") or [])

    if app and candidate_app and app.upper() == str(candidate_app).upper():
        score += 35
        reasons.append("APP_CODE_FROM_PATH_MATCH")

    if field and leaf == field:
        score += 30
        reasons.append("FIELD_LEAF_EXACT_MATCH")
        method = "PATH_EXACT"
    elif field and leaf:
        ratio = fuzz.ratio(field, leaf)
        if ratio >= 85:
            score += 20
            reasons.append(f"FIELD_LEAF_FUZZY_MATCH:{ratio}")
            method = "FUZZY"
        elif ratio >= 70:
            score += 12
            reasons.append(f"FIELD_LEAF_WEAK_FUZZY_MATCH:{ratio}")
            method = "FUZZY"

    if structure and (structure == parent or structure in tokens or structure in normalized_path):
        score += 25
        reasons.append("STRUCTURE_IN_PATH_MATCH")
    elif structure and parent:
        ratio = fuzz.ratio(structure, parent)
        if ratio >= 80:
            score += 15
            reasons.append(f"STRUCTURE_PARENT_FUZZY_MATCH:{ratio}")
            method = "FUZZY"

    if source and (source in tokens or source in normalized_path):
        score += 10
        reasons.append("CONTROLLED_SOURCE_IN_PATH_MATCH")

    if candidate.get("embedding_similarity") is not None:
        sim = float(candidate["embedding_similarity"])
        score += min(max(sim, 0.0), 1.0) * 15.0
        reasons.append(f"EMBEDDING_COSINE:{sim:.4f}")
        if method != "PATH_EXACT":
            method = "EMBEDDING_FALLBACK"

    return {**candidate, "match_score": round(score, 2), "match_method": method, "match_reasons": reasons}


def confidence(score: float, high: float = 85.0, medium: float = 65.0) -> str:
    if score >= high:
        return "HIGH"
    if score >= medium:
        return "MEDIUM"
    return "LOW"
