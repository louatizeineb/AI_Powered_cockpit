from __future__ import annotations

from sqlalchemy.orm import Session

from app.dqc.config import DQC_ENVIRONMENT
from app.dqc.models import DQCCatalogResolution, DQCDLQ, DQCEventStore, DQCResult


def save_dqc_event_store(db: Session, normalized: dict, original_event: dict) -> DQCEventStore:
    row = DQCEventStore(
        environment=DQC_ENVIRONMENT,
        event_family=normalized["event_family"],
        event_type=normalized["event_type"],
        schema_id=normalized.get("schema_id"),
        schema_version=normalized.get("schema_version"),
        source_system=normalized.get("source_system"),
        correlation_id=normalized.get("correlation_id"),
        payload_json=original_event,
        status="VALID",
    )
    db.add(row)
    db.flush()
    return row


def save_dqc_result(db: Session, raw_event_id: int, dq_result: dict) -> DQCResult:
    row = DQCResult(environment=DQC_ENVIRONMENT, **dq_result, raw_event_id=raw_event_id)
    db.add(row)
    db.flush()
    return row


def save_dqc_resolution(
    db: Session,
    *,
    event_store_id: int,
    dq_result_id: int | None,
    matched_node_id: str | None,
    matched_label: str | None,
    catalog_reference_key: str | None,
    match_method: str | None,
    confidence: float | None,
    status: str,
) -> DQCCatalogResolution:
    row = DQCCatalogResolution(
        environment=DQC_ENVIRONMENT,
        event_store_id=event_store_id,
        dq_result_id=dq_result_id,
        matched_node_id=matched_node_id,
        matched_label=matched_label,
        catalog_reference_key=catalog_reference_key,
        match_method=match_method,
        confidence=confidence,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def save_dqc_dlq_event(
    db: Session,
    *,
    topic: str,
    payload: dict,
    error_type: str,
    error_message: str,
    event_family: str | None = "data_quality",
    schema_id: str | None = None,
) -> DQCDLQ:
    row = DQCDLQ(
        environment=DQC_ENVIRONMENT,
        topic=topic,
        event_family=event_family,
        schema_id=schema_id,
        payload_json=payload,
        error_type=error_type,
        error_message=error_message,
        replay_status="PENDING",
    )
    db.add(row)
    db.flush()
    return row


def get_pending_dlq_events(db: Session, limit: int = 100) -> list[DQCDLQ]:
    return (
        db.query(DQCDLQ)
        .filter(DQCDLQ.replay_status == "PENDING")
        .order_by(DQCDLQ.id.asc())
        .limit(limit)
        .all()
    )


def mark_dlq_replayed(db: Session, dlq_id: int) -> None:
    row = db.query(DQCDLQ).filter(DQCDLQ.id == dlq_id).first()
    if row:
        row.replay_status = "REPLAYED"
        db.flush()
