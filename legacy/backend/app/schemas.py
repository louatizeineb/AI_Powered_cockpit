from typing import Any, Literal
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class FullHealthResponse(BaseModel):
    postgres: str
    neo4j: str
    marquez: str


class SearchResult(BaseModel):
    id: str
    node_id: str | None = None
    type: str
    name: str | None = None
    technical_name: str | None = None
    path: str | None = None
    source: str = "postgres"


class SearchResponse(BaseModel):
    query: str
    count: int
    results: list[SearchResult]


class GraphNode(BaseModel):
    id: str
    node_id: str | None = None
    label: str | None = None
    type: str | None = None
    kind: str | None = None
    quality: dict[str, Any] | None = None
    quality_checks: list[dict[str, Any]] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphResponse(BaseModel):
    root: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    stats: dict[str, int]


class LinkRow(BaseModel):
    src_node_id: str | None = None
    src_name_label: str | None = None
    src_name_tech: str | None = None
    src_entity_type: str | None = None
    src_data_type: str | None = None

    link_type: str

    tgt_node_id: str | None = None
    tgt_name_label: str | None = None
    tgt_name_tech: str | None = None
    tgt_entity_type: str | None = None
    tgt_data_type: str | None = None
    tgt_path: str | None = None


class SampleLinksResponse(BaseModel):
    count: int
    items: list[dict[str, Any]]


class BootstrapResponse(BaseModel):
    dry_run: bool
    links_read: int
    jobs_detected: int
    events_generated: int
    events_sent: int
    events_failed: int
    skipped_jobs_without_inputs_or_outputs: int
    sample_events: list[dict[str, Any]] = Field(default_factory=list)


class MarquezEmitResult(BaseModel):
    success: bool
    status_code: int | None = None
    response_text: str | None = None
    error: str | None = None
