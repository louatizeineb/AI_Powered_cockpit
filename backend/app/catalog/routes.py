from fastapi import APIRouter

router = APIRouter(prefix="/catalog", tags=["Catalog"])

@router.get("/health")
def catalog_health():
    return {
        "status": "ok",
        "module": "catalog",
        "message": "Catalog path parsing module is available"
    }