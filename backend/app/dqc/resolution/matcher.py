from __future__ import annotations
from app.dqc.resolution.repository import find_path_candidates
from app.dqc.resolution.scoring import score_candidate
from app.embeddings.provider import embed_text
from app.embeddings.repository import list_embeddings
from app.embeddings.vector import cosine_similarity


def _query_text(normalized: dict) -> str:
    return " | ".join(str(x) for x in [
        normalized.get("application_code_norm"),
        normalized.get("controlled_source_name_norm"),
        normalized.get("controlled_structure_name"),
        normalized.get("controlled_field_name"),
        normalized.get("target_level"),
    ] if x)


def generate_candidates(normalized: dict, use_embeddings: bool = True, limit: int = 20) -> list[dict]:
    base_candidates = find_path_candidates(normalized, limit=100)
    scored = [score_candidate(normalized, c) for c in base_candidates]

    if use_embeddings:
        query_vec = embed_text(_query_text(normalized))
        emb_rows = list_embeddings(
            app_code=normalized.get("application_code_norm"),
            target_level=normalized.get("target_level"),
            limit=3000,
        )
        existing_ids = {c.get("node_id") for c in scored}
        for row in emb_rows:
            sim = cosine_similarity(query_vec, row.get("embedding_vector") or [])
            if sim < 0.35:
                continue
            candidate = {
                "id": row.get("catalog_path_index_id"),
                "entity_level": row.get("entity_level"),
                "node_id": row.get("node_id"),
                "raw_path_full": row.get("raw_path_full"),
                "normalized_path": row.get("normalized_path"),
                "app_code_from_path": row.get("app_code_from_path"),
                "leaf_name": row.get("leaf_name"),
                "parent_name": row.get("parent_name"),
                "path_tokens": row.get("path_tokens"),
                "embedding_similarity": sim,
            }
            if candidate["node_id"] in existing_ids:
                continue
            scored.append(score_candidate(normalized, candidate))

    scored.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return scored[:limit]
