from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    BootstrapResponse,
    FullHealthResponse,
    GraphResponse,
    HealthResponse,
    SampleLinksResponse,
    SearchResponse,
)
from app.services import (
    BusinessLineageService,
    HealthService,
    OpenLineageBootstrapService,
    SearchService,
)


router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}


@router.get("/health/full", response_model=FullHealthResponse)
async def full_health():
    service = HealthService()
    return await service.full_health()


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
):
    service = SearchService()
    return service.search(q=q, limit=limit)


@router.get("/lineage/business/{node_id}", response_model=GraphResponse)
async def business_lineage(
    node_id: str,
    depth: int = Query(2, ge=1, le=10),
):
    service = BusinessLineageService()
    result = service.get_subgraph(node_id=node_id, depth=depth)

    if result["stats"]["node_count"] == 0:
        raise HTTPException(status_code=404, detail="Node not found in Neo4j")

    return result


@router.get("/lineage/{node_id}/usage")
async def usage_neighbors(node_id: str):
    service = BusinessLineageService()
    result = service.get_usage_neighbors(node_id=node_id)

    if not result["start_node"]:
        raise HTTPException(status_code=404, detail="Node not found in Neo4j")

    return result


@router.get("/lineage/links/sample", response_model=SampleLinksResponse)
async def sample_links(
    limit: int = Query(20, ge=1, le=100),
):
    service = OpenLineageBootstrapService()
    return service.sample_links(limit=limit)


@router.post("/openlineage/bootstrap", response_model=BootstrapResponse)
async def bootstrap_openlineage(
    limit: int | None = Query(None, ge=1, le=100000),
    dry_run: bool = Query(True),
    sample_size: int = Query(3, ge=0, le=20),
):
    service = OpenLineageBootstrapService()
    return await service.bootstrap(
        limit=limit,
        dry_run=dry_run,
        sample_size=sample_size,
    )


@router.get("/marquez/health", response_model=HealthResponse)
async def marquez_health():
    service = HealthService()
    status = await service.full_health()

    return {
        "status": status["marquez"],
    }

@router.get("/")
async def root():
    return {
        "message": "Data Quality Cockpit Backend is running",
        "docs": "/docs",
        "health": "/health",
    }
