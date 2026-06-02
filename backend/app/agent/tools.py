from __future__ import annotations
from app.dqc.resolution.service import process_event
from app.dqc.resolution.matcher import generate_candidates
from app.dqc.resolution.normalizer import normalize_event
from app.graphrag.retriever import retrieve_catalog_evidence
from app.dqc.resolution.repository import list_dlq, list_resolved, approve_match, reject_match


def tool_process_dqc_event(event: dict) -> dict:
    return process_event(event, source_system="agent_tool")


def tool_generate_candidates(event: dict) -> dict:
    normalized = normalize_event(event, source_system="agent_tool_preview")
    candidates = generate_candidates(normalized, use_embeddings=True)
    return {"normalized": normalized, "candidates": candidates[:10]}


def tool_retrieve_graphrag_evidence(event: dict) -> dict:
    normalized = normalize_event(event, source_system="agent_tool_preview")
    candidates = generate_candidates(normalized, use_embeddings=True)
    return retrieve_catalog_evidence(normalized, candidates)


def tool_list_unresolved(limit: int = 10) -> dict:
    return {"items": list_dlq(limit)}


def tool_list_resolved(limit: int = 10) -> dict:
    return {"items": list_resolved(limit)}


def tool_approve_match(resolved_id: int, reviewer: str, note: str = "") -> dict:
    return approve_match(resolved_id, reviewer, note)


def tool_reject_match(resolved_id: int, reviewer: str, reason: str) -> dict:
    return reject_match(resolved_id, reviewer, reason)
