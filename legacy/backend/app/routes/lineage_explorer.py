from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query


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


@router.get("/search", response_model=LineageExplorerSearchResponse)
async def search_lineage_entities(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
):
    service = LineageExplorerService()
    return service.search(q=q, limit=limit)


@router.get("/node/{node_id}/neighbors", response_model=LineageExplorerNeighborsResponse)
async def lineage_entity_neighbors(
    node_id: str,
    direction: str = Query(..., pattern="^(upstream|downstream)$"),
    limit: int = Query(50, ge=1, le=200),
):
    service = LineageExplorerService()
    result = service.neighbors(node_id=node_id, direction=direction, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail="Lineage entity not found")
    return result
