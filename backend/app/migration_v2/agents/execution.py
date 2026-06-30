from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutableAgentResult:
    export_id: str
    workflow_run_id: str
    agent_name: str
    status: str
    mode: str
    summary: dict[str, Any] = field(default_factory=dict)
    proposals: list[Any] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    model_name: str | None = None
    llm_call_count: int = 0
    fallback_count: int = 0


@dataclass
class MappingProposal:
    raw_table_name: str
    raw_column_name: str
    current_canonical_field: str | None
    proposed_canonical_field: str | None
    proposed_action: str
    confidence: float
    rationale: str
    missing_evidence: list[str] = field(default_factory=list)
    human_question: str = ""
    candidate_columns: list[dict[str, Any]] = field(default_factory=list)
    guardrail_actions: list[str] = field(default_factory=list)
    raw_model_response: str = ""


@dataclass
class AgentContext:
    engine: Any
    workflow_repository: Any
    tool_runtime: Any
    state: Any
    contract_path: str
    env_config_path: str
    require_llm: bool = False
    refresh_tools: bool = False
