from __future__ import annotations

from _bootstrap import bootstrap_backend

bootstrap_backend()

from backend.app.db import SessionLocal
from backend.app.dqc.producer import publish_event
from backend.app.dqc.repositories import get_pending_dlq_events, mark_dlq_replayed
from backend.app.dqc.topics import DQC_RAW_TOPIC


if __name__ == "__main__":
    db = SessionLocal()
    try:
        rows = get_pending_dlq_events(db, limit=50)
        for row in rows:
            publish_event(DQC_RAW_TOPIC, row.payload_json, key=str(row.id))
            mark_dlq_replayed(db, row.id)
            print(f"Replayed DLQ event {row.id}")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
