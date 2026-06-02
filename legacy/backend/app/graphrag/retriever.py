from __future__ import annotations
from sqlalchemy import text
from app.db import SessionLocal


def retrieve_catalog_evidence(normalized: dict, candidates: list[dict], top_k: int = 5) -> dict:
    """GraphRAG evidence pack. Uses path index and candidate paths now; can be extended with Neo4j neighbors."""
    evidence = []
    for c in candidates[:top_k]:
        evidence.append({
            "node_id": c.get("node_id"),
            "entity_level": c.get("entity_level"),
            "path_full": c.get("raw_path_full"),
            "score": c.get("match_score"),
            "method": c.get("match_method"),
            "reasons": c.get("match_reasons"),
        })
    return {
        "query": normalized,
        "candidate_evidence": evidence,
        "retrieval_note": "Evidence retrieved from catalog_path_index and precomputed embedding store. Neo4j neighborhood retrieval can be added here if graph is available.",
    }
