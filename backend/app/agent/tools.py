from __future__ import annotations
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.dqc.resolution.service import process_event
from app.dqc.resolution.matcher import generate_candidates
from app.dqc.resolution.normalizer import normalize_event
from app.graphrag.retriever import retrieve_catalog_evidence
from app.dqc.resolution.repository import list_dlq, list_resolved


class DQCToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DQCEventToolInput(DQCToolInput):
    event: dict[str, Any]


class DQCLimitToolInput(DQCToolInput):
    limit: int = Field(default=10, ge=1, le=1000)


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    input_model: type[DQCToolInput]
    mutates_resolution: bool = False
    callable: Callable[[DQCToolInput], dict[str, Any]]


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


def _call_process(payload: DQCToolInput) -> dict[str, Any]:
    typed = DQCEventToolInput.model_validate(payload)
    return tool_process_dqc_event(typed.event)


def _call_candidates(payload: DQCToolInput) -> dict[str, Any]:
    typed = DQCEventToolInput.model_validate(payload)
    return tool_generate_candidates(typed.event)


def _call_evidence(payload: DQCToolInput) -> dict[str, Any]:
    typed = DQCEventToolInput.model_validate(payload)
    return tool_retrieve_graphrag_evidence(typed.event)


def _call_unresolved(payload: DQCToolInput) -> dict[str, Any]:
    typed = DQCLimitToolInput.model_validate(payload)
    return tool_list_unresolved(typed.limit)


def _call_resolved(payload: DQCToolInput) -> dict[str, Any]:
    typed = DQCLimitToolInput.model_validate(payload)
    return tool_list_resolved(typed.limit)


DQC_TOOL_REGISTRY: dict[str, ToolSpec] = {
    "process_dqc_event": ToolSpec(name="process_dqc_event", input_model=DQCEventToolInput, callable=_call_process),
    "generate_candidates": ToolSpec(name="generate_candidates", input_model=DQCEventToolInput, callable=_call_candidates),
    "retrieve_graphrag_evidence": ToolSpec(name="retrieve_graphrag_evidence", input_model=DQCEventToolInput, callable=_call_evidence),
    "list_unresolved": ToolSpec(name="list_unresolved", input_model=DQCLimitToolInput, callable=_call_unresolved),
    "list_resolved": ToolSpec(name="list_resolved", input_model=DQCLimitToolInput, callable=_call_resolved),
}


def validate_tool_input(tool_name: str, payload: dict[str, Any]) -> DQCToolInput:
    try:
        spec = DQC_TOOL_REGISTRY[tool_name]
    except KeyError as exc:
        raise PermissionError(f"DQC agent tool is not registered: {tool_name}") from exc
    return spec.input_model.model_validate(payload)


def run_dqc_tool(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    typed = validate_tool_input(tool_name, payload)
    try:
        spec = DQC_TOOL_REGISTRY[tool_name]
        return spec.callable(typed)
    except ValidationError:
        raise
