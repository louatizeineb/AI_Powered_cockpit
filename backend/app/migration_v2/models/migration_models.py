from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class MigrationExportRun:
    """Application model for a registered migration_v2 export run."""

    export_id: str
    export_path: str | None = None
    contract_version: str | None = None
    status: str = "registered"
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MigrationFinding:
    """Application model for validation, audit, and mapping findings."""

    severity: str
    category: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
