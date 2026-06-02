from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response
from neo4j.exceptions import ServiceUnavailable


def _load_from_path(module_name: str, relative_path: str):
    path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schemas = _load_from_path("lineage_explorer_schemas_runtime", "schemas/lineage_explorer.py")
service_module = _load_from_path("lineage_explorer_service_runtime", "services/lineage_explorer_service.py")

LineageExplorerSearchResponse = schemas.LineageExplorerSearchResponse
LineageExplorerNeighborsResponse = schemas.LineageExplorerNeighborsResponse
LineageExplorerService = service_module.LineageExplorerService

router = APIRouter(prefix="/lineage/explorer", tags=["Lineage Explorer"])


def _raise_graph_unavailable(exc: ServiceUnavailable) -> None:
    raise HTTPException(
        status_code=503,
        detail="Lineage graph unavailable. Start the Neo4j service and retry.",
    ) from exc


def _apply_response_metadata(response: Response, service: LineageExplorerService) -> None:
    metadata = service.last_metadata
    if metadata.get("cache"):
        response.headers["X-Cache"] = metadata["cache"]
    if metadata.get("graph_version"):
        response.headers["X-Graph-Version"] = metadata["graph_version"]
    if metadata.get("server_timing"):
        response.headers["Server-Timing"] = metadata["server_timing"]


@router.get("/search", response_model=LineageExplorerSearchResponse)
async def search_lineage_entities(
    response: Response,
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
):
    service = LineageExplorerService()
    try:
        result = service.search(q=q, limit=limit)
    except ServiceUnavailable as exc:
        _raise_graph_unavailable(exc)
    _apply_response_metadata(response, service)
    return result


@router.get("/node/{node_id}/neighbors", response_model=LineageExplorerNeighborsResponse)
async def lineage_entity_neighbors(
    response: Response,
    node_id: str,
    direction: str = Query(..., pattern="^(upstream|downstream)$"),
    limit: int = Query(50, ge=1, le=200),
):
    service = LineageExplorerService()
    try:
        result = service.neighbors(node_id=node_id, direction=direction, limit=limit)
    except ServiceUnavailable as exc:
        _raise_graph_unavailable(exc)
    if result is None:
        raise HTTPException(status_code=404, detail="Lineage entity not found")
    _apply_response_metadata(response, service)
    return result


@router.get("/node/{node_id}/source-context", response_model=LineageExplorerNeighborsResponse)
async def lineage_source_context(
    response: Response,
    node_id: str,
    catalog_offset: int = Query(0, ge=0),
    catalog_limit: int = Query(500, ge=1, le=2000),
    consumer_limit: int = Query(300, ge=1, le=1000),
):
    service = LineageExplorerService()
    try:
        result = service.source_context(
            node_id=node_id,
            catalog_offset=catalog_offset,
            catalog_limit=catalog_limit,
            consumer_limit=consumer_limit,
        )
    except ServiceUnavailable as exc:
        _raise_graph_unavailable(exc)
    if result is None:
        raise HTTPException(status_code=404, detail="Lineage source not found")
    _apply_response_metadata(response, service)
    return result
