from __future__ import annotations
from datetime import datetime
from typing import Any
from sqlalchemy import text
from app.db import SessionLocal
from app.config import get_settings

settings = get_settings()


def log_event(
    run_id: str | None,
    stage: str,
    level: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    print(f"[{datetime.utcnow().isoformat()}] [{level}] [{stage}] {message}")
    if not settings.pipeline_log_to_db:
        return
    try:
        with SessionLocal() as db:
            db.execute(text("""
                INSERT INTO pipeline_logs(run_id, stage, level, message, details)
                VALUES (:run_id, :stage, :level, :message, CAST(:details AS JSONB))
            """), {
                "run_id": run_id,
                "stage": stage,
                "level": level,
                "message": message,
                "details": __import__('json').dumps(details or {}),
            })
            db.commit()
    except Exception as exc:
        print(f"[WARN] failed to write pipeline log to DB: {exc}")
