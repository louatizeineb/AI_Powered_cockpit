from fastapi import APIRouter, Query
from sqlalchemy import text
from app.db import SessionLocal

router = APIRouter(prefix="/observability", tags=["Observability"])


@router.get("/logs")
def list_logs(run_id: str | None = None, limit: int = Query(100, le=1000)):
    where = "WHERE run_id = :run_id" if run_id else ""
    params = {"limit": limit}
    if run_id:
        params["run_id"] = run_id
    with SessionLocal() as db:
        rows = db.execute(text(f"""
            SELECT id, run_id, stage, level, message, details, created_at
            FROM pipeline_logs
            {where}
            ORDER BY id DESC
            LIMIT :limit
        """), params).mappings().all()
    return {"items": [dict(r) for r in rows]}
