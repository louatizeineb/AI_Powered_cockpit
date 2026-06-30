from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.common.azure_openai import build_chat_llm_client, chat_llm_provider
from app.config import Settings


ALLOWED_POLICIES = {"accept", "quarantine", "exclude", "repair", "needs_human", "block"}
_LAST_LLM_CALL_AT = 0.0


@dataclass
class AgentProposal:
    issue_id: str
    issue_type: str | None
    proposed_policy: str
    confidence: float
    rationale: str
    missing_evidence: list[str] = field(default_factory=list)
    human_question: str = ""
    guardrail_actions: list[str] = field(default_factory=list)
    raw_model_response: str = ""
    fallback_used: bool = False


@dataclass
class AgentQueryIntent:
    query_id: str
    purpose: str
    sql: str
    parameters: dict[str, Any] = field(default_factory=dict)
    safety: str = "read_only_select"


@dataclass
class AgentEvidencePlan:
    issue_id: str
    issue_type: str | None
    objective: str
    required_evidence: list[str] = field(default_factory=list)
    planned_queries: list[AgentQueryIntent] = field(default_factory=list)
    planned_tools: list[str] = field(default_factory=list)
    repair_strategy: str = ""
    risk_flags: list[str] = field(default_factory=list)


@dataclass
class AgentRunResult:
    export_id: str
    agent_name: str
    mode: str
    model_name: str | None
    reviewed_count: int
    proposal_count: int
    llm_call_count: int
    fallback_count: int
    proposals: list[AgentProposal]
    evidence_plans: list[AgentEvidencePlan] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status: str = "completed"


def llm_settings() -> Settings:
    return Settings(NEO4J_PASSWORD=os.getenv("NEO4J_PASSWORD", "unused"))


def llm_config_status(settings: Settings | None = None) -> tuple[bool, str]:
    settings = settings or llm_settings()
    provider = chat_llm_provider(settings)
    if provider == "openai":
        required = {
            "OPENAI_API_KEY": settings.openai_api_key,
            "OPENAI_CHAT_MODEL": settings.openai_chat_model,
        }
    else:
        required = {
            "AZURE_OPENAI_ENDPOINT": settings.azure_openai_endpoint,
            "AZURE_OPENAI_API_KEY": settings.azure_openai_api_key,
            "AZURE_OPENAI_CHAT_DEPLOYMENT": settings.azure_openai_chat_deployment,
        }
    missing = [key for key, value in required.items() if not value]
    if missing:
        return False, f"{provider} missing " + ", ".join(missing)
    placeholder_tokens = {"your-resource-name", "your-api-key", "your-chat-deployment", "sk-your-openai-api-key"}
    for key, value in required.items():
        lowered = str(value).lower()
        if any(token in lowered for token in placeholder_tokens):
            return False, f"{key} appears to contain placeholder config"
    return True, f"{provider} configured"


def llm_is_configured(settings: Settings | None = None) -> bool:
    configured, _reason = llm_config_status(settings)
    return configured


def call_chat_llm(system_prompt: str, user_prompt: str) -> tuple[str, str]:
    global _LAST_LLM_CALL_AT
    settings = llm_settings()
    if not llm_is_configured(settings):
        raise RuntimeError("Azure/OpenAI chat config is missing.")
    client, model_name, provider, timeout_seconds = build_chat_llm_client(settings)
    if hasattr(client, "with_options"):
        client = client.with_options(timeout=timeout_seconds, max_retries=0)
    delay = max(0.0, float(settings.llm_min_seconds_between_calls or 0))
    elapsed = time.monotonic() - _LAST_LLM_CALL_AT
    if delay and elapsed < delay:
        time.sleep(delay - elapsed)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    request_payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": max(64, int(settings.llm_max_completion_tokens or 700)),
    }
    reasoning_effort = (settings.llm_reasoning_effort or "").strip().lower()
    if reasoning_effort and reasoning_effort != "default":
        request_payload["reasoning_effort"] = reasoning_effort
    response = None
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            response = client.chat.completions.create(**request_payload)
            break
        except Exception as exc:  # noqa: PERF203
            last_error = exc
            message = str(exc).lower()
            if "reasoning_effort" in message and "reasoning_effort" in request_payload:
                request_payload.pop("reasoning_effort", None)
                continue
            if "max_completion_tokens" in message and "max_completion_tokens" in request_payload:
                request_payload.pop("max_completion_tokens", None)
                continue
            if ("response_format" in message or "json_object" in message or "unsupported" in message) and "response_format" in request_payload:
                request_payload.pop("response_format", None)
                continue
            raise
    if response is None:
        raise RuntimeError(f"LLM request failed after optional-parameter fallback: {last_error}") from last_error
    _LAST_LLM_CALL_AT = time.monotonic()
    content = response.choices[0].message.content or ""
    if not content.strip():
        retry_payload = dict(request_payload)
        retry_payload.pop("reasoning_effort", None)
        retry_payload["max_completion_tokens"] = max(
            int(retry_payload.get("max_completion_tokens") or 0),
            min(1600, max(900, int(settings.llm_max_completion_tokens or 700) * 2)),
        )
        if delay:
            time.sleep(delay)
        response = client.chat.completions.create(**retry_payload)
        _LAST_LLM_CALL_AT = time.monotonic()
        content = response.choices[0].message.content or ""
    if not content.strip():
        raise RuntimeError("LLM returned an empty response.")
    return content, f"{provider}:{model_name}"


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object.")
    return parsed


def proposal_from_payload(
    payload: dict[str, Any],
    fallback_used: bool = False,
    raw_model_response: str = "",
) -> AgentProposal:
    missing = payload.get("missing_evidence") or []
    if isinstance(missing, str):
        missing = [missing]
    confidence = payload.get("confidence", 0.0)
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    return AgentProposal(
        issue_id=str(payload.get("issue_id") or ""),
        issue_type=payload.get("issue_type"),
        proposed_policy=str(payload.get("proposed_policy") or "needs_human"),
        confidence=max(0.0, min(1.0, confidence_value)),
        rationale=str(payload.get("rationale") or "No rationale provided."),
        missing_evidence=[str(item) for item in missing],
        human_question=str(payload.get("human_question") or ""),
        raw_model_response=raw_model_response,
        fallback_used=fallback_used,
    )


def enforce_guardrails(proposal: AgentProposal, item: dict[str, Any]) -> AgentProposal:
    actions: list[str] = []
    severity = str(item.get("severity") or "").lower()
    relationship_type = str(item.get("relationship_type") or "")
    issue_type = str(item.get("issue_type") or "")
    evidence = item.get("evidence") or {}

    if proposal.proposed_policy not in ALLOWED_POLICIES:
        actions.append(f"Invalid policy `{proposal.proposed_policy}` downgraded to needs_human.")
        proposal.proposed_policy = "needs_human"

    if severity == "high" and proposal.proposed_policy == "accept" and not has_verified_blank_baseline_exception(evidence):
        actions.append("High-severity accept proposal downgraded to needs_human.")
        proposal.proposed_policy = "needs_human"

    if relationship_type == "HAS_FIELD" and proposal.proposed_policy != "repair" and not has_verified_blank_baseline_exception(evidence):
        actions.append("HAS_FIELD parity issue must remain repair until exact missing edge evidence exists.")
        proposal.proposed_policy = "repair"

    if relationship_type == "IMPLEMENTS" and proposal.proposed_policy not in {"needs_human", "repair"}:
        actions.append("IMPLEMENTS parity issue must remain needs_human or repair until edge-level diff exists.")
        proposal.proposed_policy = "needs_human"

    if proposal.proposed_policy == "repair" and not has_repair_evidence(evidence, relationship_type, issue_type):
        if relationship_type != "HAS_FIELD":
            actions.append("Repair proposal without repair evidence downgraded to needs_human.")
            proposal.proposed_policy = "needs_human"

    proposal.guardrail_actions.extend(actions)
    return proposal


def has_repair_evidence(evidence: dict[str, Any], relationship_type: str, issue_type: str) -> bool:
    if relationship_type == "HAS_FIELD":
        return False
    return bool(evidence.get("src_node_id") and evidence.get("tgt_node_id")) or "missing_hierarchy_edge" in issue_type


def has_verified_blank_baseline_exception(evidence: dict[str, Any]) -> bool:
    return (
        str(evidence.get("edge_level_diff") or "").lower() == "zero_real_missing_edges"
        and int(evidence.get("legacy_blank_rows") or 0) > 0
    )


def compact_json(value: Any, max_chars: int = 6000) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "...[truncated]"
