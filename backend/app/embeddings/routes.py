from fastapi import APIRouter
from app.embeddings.service import generate_catalog_embeddings

router = APIRouter(prefix="/embeddings", tags=["Embeddings"])


@router.post("/catalog/generate")
def generate(limit: int | None = None):
    return generate_catalog_embeddings(limit=limit)
