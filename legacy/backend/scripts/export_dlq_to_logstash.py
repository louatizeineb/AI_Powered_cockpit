from __future__ import annotations

from _bootstrap import bootstrap_backend

bootstrap_backend()

from backend.app.db import SessionLocal
from backend.app.dqc.dlq import emit_dlq_observability_log
from backend.app.dqc.models import DQCDLQ

if __name__ == "__main__":
    db = SessionLocal()
    try:
        rows = db.query(DQCDLQ).order_by(DQCDLQ.id.asc()).all()
        for row in rows:
            emit_dlq_observability_log(
                dlq_id=row.id,
                topic=row.topic,
                payload=row.payload_json,
                error_type=row.error_type,
                error_message=row.error_message,
            )
            print(f"Exported DLQ row {row.id} to structured log")
    finally:
        db.close()
