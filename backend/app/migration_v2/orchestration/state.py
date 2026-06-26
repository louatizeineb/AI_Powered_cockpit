from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkflowStatus(StrEnum):
    RECEIVED = "received"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    FAILED = "failed"
    READY = "ready"
    PUBLISHED = "published"
    CANCELLED = "cancelled"


class WorkflowPhase(StrEnum):
    RECEIVED = "received"
    REGISTERED = "registered"
    PROFILED = "profiled"
    DRIFT_REVIEW = "drift_review"
    MAPPED = "mapped"
    STAGED = "staged"
    VALIDATED = "validated"
    QUEUE_REVIEW = "queue_review"
    CANDIDATE_BUILT = "candidate_built"
    AUDITED = "audited"
    BENCHMARKED = "benchmarked"
    READY = "ready"
    PUBLISHED = "published"


class MigrationRunState(BaseModel):
    """Durable, serializable state shared by migration_v2 workflow nodes."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    run_id: str
    thread_id: str
    export_id: str
    export_path: str | None = None
    export_fingerprint: str
    contract_version: str
    workflow_version: str = "1.0.0"
    trigger_type: str = "manual"
    status: WorkflowStatus = WorkflowStatus.RECEIVED
    current_phase: WorkflowPhase = WorkflowPhase.RECEIVED
    discovered_files: list[dict[str, Any]] = Field(default_factory=list)
    drift_findings: list[dict[str, Any]] = Field(default_factory=list)
    agent_proposals: list[dict[str, Any]] = Field(default_factory=list)
    validation_counts: dict[str, int] = Field(default_factory=dict)
    publication_counts: dict[str, int] = Field(default_factory=dict)
    approval_requirements: list[dict[str, Any]] = Field(default_factory=list)
    pending_approval: dict[str, Any] | None = None
    approval_decisions: list[dict[str, Any]] = Field(default_factory=list)
    agent_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    generated_artifacts: list[str] = Field(default_factory=list)
    graph_version: str | None = None
    search_version: str | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("run_id", "thread_id", "export_id", "export_fingerprint", "contract_version")
    @classmethod
    def require_nonempty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        return cleaned

    def snapshot(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class MigrationGraphState(TypedDict, total=False):
    """LangGraph state schema mirroring MigrationRunState's JSON representation."""

    run_id: str
    thread_id: str
    export_id: str
    export_path: str | None
    export_fingerprint: str
    contract_version: str
    workflow_version: str
    trigger_type: str
    status: str
    current_phase: str
    discovered_files: list[dict[str, Any]]
    drift_findings: list[dict[str, Any]]
    agent_proposals: list[dict[str, Any]]
    validation_counts: dict[str, int]
    publication_counts: dict[str, int]
    approval_requirements: list[dict[str, Any]]
    pending_approval: dict[str, Any] | None
    approval_decisions: list[dict[str, Any]]
    agent_results: dict[str, dict[str, Any]]
    generated_artifacts: list[str]
    graph_version: str | None
    search_version: str | None
    errors: list[dict[str, Any]]
    updated_at: str
