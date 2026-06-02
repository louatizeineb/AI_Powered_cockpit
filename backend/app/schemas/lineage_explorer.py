from typing import Any, Literal

from pydantic import BaseModel, Field


LineageDirection = Literal["upstream", "downstream"]


class LineageExplorerNode(BaseModel):
    id: str
    node_id: str
    label: str
    technical_name: str | None = None
    type: str
    category: str
    entity_type: str | None = None
    data_type: str | None = None
    path_full: str | None = None
    path_type: str | None = None
    parent_node_id: str | None = None
    parent_label: str | None = None
    parent_type: str | None = None
    path: str | None = None
    visual_role: str | None = None
    group_id: str | None = None
    group_type: str | None = None
    group_label: str | None = None
    has_upstream: bool = False
    has_downstream: bool = False
    properties: dict[str, Any] = Field(default_factory=dict)


class LineageExplorerEdge(BaseModel):
    id: str
    source: str
    target: str
    raw_source: str | None = None
    raw_target: str | None = None
    type: str
    raw_type: str | None = None
    direction: LineageDirection
    visual_source: str
    visual_target: str
    is_visual_reversed: bool = False
    properties: dict[str, Any] = Field(default_factory=dict)


class LineageExplorerSearchResponse(BaseModel):
    query: str
    count: int
    results: list[LineageExplorerNode]


class LineageExplorerNeighborsResponse(BaseModel):
    center: LineageExplorerNode
    nodes: list[LineageExplorerNode]
    edges: list[LineageExplorerEdge]
