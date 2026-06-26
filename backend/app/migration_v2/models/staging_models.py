from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CatalogObjectStaging:
    """Canonical staged catalog object independent of raw DataGalaxy column names."""

    export_id: str
    node_id: str
    object_type: str
    parent_node_id: str | None = None
    status: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogRelationshipStaging:
    """Canonical staged relationship independent of raw DataGalaxy link naming."""

    export_id: str
    src_node_id: str
    tgt_node_id: str
    relationship_type: str
    link_type: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
