from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DQCAgentCapability(StrEnum):
    READ_DQC = "read_dqc"
    READ_CATALOG_EVIDENCE = "read_catalog_evidence"
    PROCESS_EVENT = "process_event"
    PROPOSE = "propose"


@dataclass(frozen=True)
class DQCAgentManifest:
    name: str = "DQCResolutionAgent"
    version: str = "1.0.0"
    mission: str = "Explain DQC resolution evidence and propose safe reviewer actions."
    capabilities: frozenset[DQCAgentCapability] = frozenset(
        {
            DQCAgentCapability.READ_DQC,
            DQCAgentCapability.READ_CATALOG_EVIDENCE,
            DQCAgentCapability.PROCESS_EVENT,
            DQCAgentCapability.PROPOSE,
        }
    )
    allowed_tools: tuple[str, ...] = (
        "process_dqc_event",
        "generate_candidates",
        "retrieve_graphrag_evidence",
        "list_unresolved",
        "list_resolved",
    )
    write_scopes: tuple[str, ...] = ("dqc_agent_run", "dqc_agent_proposal")
    requires_approval_for: tuple[str, ...] = ("approve_match", "reject_match", "replay_after_fix")
    max_llm_calls: int = 1
    deterministic_fallback: bool = True

    @property
    def manifest_id(self) -> str:
        return f"{self.name}:{self.version}"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = sorted(str(item) for item in self.capabilities)
        return payload


DQC_AGENT_MANIFEST = DQCAgentManifest()


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    mode: str
    answer: str
    recommended_action: str
    confidence: str | float | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    missing_evidence: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    proposal_id: int | None = None


@dataclass
class DQCAgentProposal:
    subject_type: str
    subject_id: int | None
    proposed_action: str
    confidence: str | float | None
    rationale: str
    missing_evidence: list[str] = field(default_factory=list)
    human_question: str = ""
    guardrail_actions: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    raw_model_response: str = ""

    def as_response(
        self,
        *,
        mode: str,
        answer: str,
        next_steps: list[str],
        citations: list[dict[str, Any]] | None = None,
        proposal_id: int | None = None,
    ) -> AgentResponse:
        return AgentResponse(
            mode=mode,
            answer=answer,
            recommended_action=self.proposed_action,
            confidence=self.confidence,
            evidence=self.evidence,
            missing_evidence=self.missing_evidence,
            next_steps=next_steps,
            guardrails=self.guardrail_actions,
            citations=citations or [],
            proposal_id=proposal_id,
        )
