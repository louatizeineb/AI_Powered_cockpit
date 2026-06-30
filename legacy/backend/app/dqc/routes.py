from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.dqc.models import DQCCatalogResolution, DQCDLQ, DQCEventStore, DQCResult
from backend.app.dqc.producer import publish_event
from backend.app.dqc.synthetic_generator import make_bad_counts_event, make_valid_event
from backend.app.dqc.topics import DQC_RAW_TOPIC

router = APIRouter(prefix="/dqc", tags=["dqc"])


@router.get("/recent")
def get_recent_dqc_events(db: Session = Depends(get_db)):
    return db.query(DQCEventStore).order_by(DQCEventStore.id.desc()).limit(50).all()


@router.get("/results")
def get_dqc_results(db: Session = Depends(get_db)):
    return db.query(DQCResult).order_by(DQCResult.id.desc()).limit(50).all()


@router.get("/dlq")
def get_dqc_dlq(db: Session = Depends(get_db)):
    return db.query(DQCDLQ).order_by(DQCDLQ.id.desc()).limit(50).all()


@router.get("/resolutions")
def get_dqc_resolutions(db: Session = Depends(get_db)):
    return db.query(DQCCatalogResolution).order_by(DQCCatalogResolution.id.desc()).limit(50).all()


@router.get("/summary")
def get_dqc_summary(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            """
            SELECT
                application_code,
                quality_status,
                count(*) AS check_count,
                round(avg(computed_score)::numeric, 2) AS avg_score,
                sum(ok_count) AS total_ok,
                sum(ko_count) AS total_ko
            FROM dqc_result
            GROUP BY application_code, quality_status
            ORDER BY avg_score ASC NULLS LAST
            """
        )
    ).mappings().all()
    return list(rows)


@router.get("/resolution-summary")
def get_dqc_resolution_summary(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            """
            SELECT status, matched_label, count(*) AS count
            FROM dqc_catalog_resolution
            GROUP BY status, matched_label
            ORDER BY count DESC
            """
        )
    ).mappings().all()
    return list(rows)


@router.get("/dlq-summary")
def get_dqc_dlq_summary(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            """
            SELECT error_type, count(*) AS count
            FROM dqc_event_dlq
            GROUP BY error_type
            ORDER BY count DESC
            """
        )
    ).mappings().all()
    return list(rows)


@router.post("/test/send-valid")
def send_valid_test_event():
    event = make_valid_event()
    publish_event(DQC_RAW_TOPIC, event, key=event["metadata"]["eventId"])
    return {"published": True, "topic": DQC_RAW_TOPIC, "event": event}


@router.post("/test/send-bad")
def send_bad_test_event():
    event = make_bad_counts_event()
    publish_event(DQC_RAW_TOPIC, event, key=event["metadata"]["eventId"])
    return {"published": True, "topic": DQC_RAW_TOPIC, "event": event}
