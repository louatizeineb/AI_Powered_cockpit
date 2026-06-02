from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, UploadFile, File, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.dqc.resolution.service import process_event, process_many
from app.dqc.resolution.ingest import read_dqc_file, SUPPORTED_DQC_EXTENSIONS
from app.dqc.resolution import repository as repo

settings = get_settings()
router = APIRouter(prefix="/dqc-resolution", tags=["DQC Resolution"])


class DatabaseConnectRequest(BaseModel):
    table_name: str | None = None
    limit: int = Field(default=1000, ge=1, le=settings.dqc_database_max_rows)


class ReviewApproveRequest(BaseModel):
    reviewer: str = "human"
    note: str | None = None


class ReviewRejectRequest(BaseModel):
    reviewer: str = "human"
    reason: str = "Rejected by reviewer"


def _safe_filename(name: str | None) -> str:
    raw = name or "dqc-upload"
    stem = Path(raw).stem or "dqc-upload"
    suffix = Path(raw).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._") or "dqc-upload"
    return f"{stem}-{uuid4().hex[:10]}{suffix}"


async def _save_upload(file: UploadFile, target: Path) -> int:
    total = 0
    with target.open("wb") as output:
        while chunk := await file.read(settings.dqc_upload_chunk_bytes):
            total += len(chunk)
            if total > settings.dqc_upload_max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Uploaded file exceeds the {settings.dqc_upload_max_bytes // (1024 * 1024)} MB limit",
                )
            output.write(chunk)
    if total == 0:
        raise ValueError("Uploaded file is empty")
    return total


@router.post("/process/event")
def process_single_event(event: dict):
    return process_event(event, source_system="api")


@router.post("/reset-workspace")
def reset_workspace():
    try:
        return repo.reset_workspace()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DQC workspace reset failed: {exc}") from exc


@router.post("/upload")
async def upload_dqc_file(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_DQC_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file format '{suffix or 'unknown'}'. "
                "Supported formats: CSV, JSON, JSONL, Parquet, PQ."
            ),
        )

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / _safe_filename(file.filename)

    try:
        bytes_received = await _save_upload(file, target)
        events = read_dqc_file(target)
        stats = process_many(events, source_system=f"upload:{file.filename}")
        return {
            "status": "completed",
            "filename": file.filename,
            "saved_as": str(target),
            "bytes_received": bytes_received,
            "rows_detected": len(events),
            "result": stats,
        }
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"DQC upload processing failed for '{file.filename}': {exc}",
        ) from exc
    finally:
        await file.close()


@router.post("/connect/database")
def connect_database(payload: DatabaseConnectRequest):
    table = payload.table_name or settings.dqc_default_table
    # Keep the table name conservative because it is interpolated as an identifier.
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise HTTPException(status_code=400, detail="Invalid table name")

    try:
        with SessionLocal() as db:
            rows = db.execute(
                text(f'SELECT * FROM "{table}" LIMIT :limit'),
                {"limit": payload.limit},
            ).mappings().all()
        events = [{str(k).lower(): v for k, v in dict(r).items()} for r in rows]
        stats = process_many(events, source_system=f"database:{table}")
        return {"status": "completed", "table_name": table, "rows_detected": len(events), "result": stats}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DQC database processing failed: {exc}") from exc


@router.get("/resolved")
def list_resolved(limit: int = Query(100, ge=1, le=1000)):
    return {"items": repo.list_resolved(limit=limit)}


@router.get("/unresolved")
def list_unresolved(limit: int = Query(100, ge=1, le=1000)):
    return {"items": repo.list_dlq(limit=limit)}


@router.post("/review/{resolved_id}/approve")
def approve(resolved_id: int, payload: ReviewApproveRequest | None = None):
    payload = payload or ReviewApproveRequest()
    return repo.approve_match(resolved_id, payload.reviewer, payload.note)


@router.post("/review/{resolved_id}/reject")
def reject(resolved_id: int, payload: ReviewRejectRequest | None = None):
    payload = payload or ReviewRejectRequest()
    return repo.reject_match(resolved_id, payload.reviewer, payload.reason)
