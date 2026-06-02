from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/graphrag", tags=["GraphRAG"])


class GraphRAGRequest(BaseModel):
    query: str
    app_code: str | None = None
    target_level: str | None = None
    limit: int = 10


@router.get("/health")
def graphrag_health():
    return {
        "status": "ok",
        "module": "graphrag"
    }


@router.post("/retrieve")
def retrieve_context(payload: GraphRAGRequest):
    """
    Temporary safe endpoint.

    Later, this will call the real GraphRAG retriever.
    For now, it keeps the backend bootable.
    """
    return {
        "status": "ok",
        "query": payload.query,
        "app_code": payload.app_code,
        "target_level": payload.target_level,
        "limit": payload.limit,
        "message": "GraphRAG route is active. Retriever implementation can be connected later."
    }