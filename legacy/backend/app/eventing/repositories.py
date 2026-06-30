from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.eventing.config import EVENT_ENVIRONMENT
from backend.app.eventing.models import (
    DataQualityCheckResult,
    EventCatalogResolution,
    EventDLQ,
    EventStore,
    PipelineRun,
)


def save_event_store(db: Session, normalized: dict, original_event: dict) -> EventStore:
    row = EventStore(
        environment=EVENT_ENVIRONMENT,
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
    db.commit()
    db.refresh(row)
    return row


def save_dlq_event(
    db: Session,
    topic: str,
    payload: dict,
    error_type: str,
    error_message: str,
    event_family: str | None = None,
    schema_id: str | None = None,
) -> EventDLQ:
    row = EventDLQ(
        environment=EVENT_ENVIRONMENT,
        topic=topic,
        event_family=event_family,
        schema_id=schema_id,
        payload_json=payload,
        error_type=error_type,
        error_message=error_message,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_dataquality_result(db: Session, raw_event_id: int, dq_result: dict) -> DataQualityCheckResult:
    row = DataQualityCheckResult(environment=EVENT_ENVIRONMENT, **dq_result, raw_event_id=raw_event_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_pipeline_run(db: Session, raw_event_id: int, pipeline_run: dict) -> PipelineRun:
    row = PipelineRun(environment=EVENT_ENVIRONMENT, **pipeline_run, raw_event_id=raw_event_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_resolution(
    db: Session,
    event_store_id: int,
    event_family: str,
    matched_node_id: str | None,
    matched_label: str | None,
    catalog_reference_key: str | None,
    match_method: str | None,
    confidence: float | None,
    status: str,
) -> EventCatalogResolution:
    row = EventCatalogResolution(
        environment=EVENT_ENVIRONMENT,
        event_store_id=event_store_id,
        event_family=event_family,
        matched_node_id=matched_node_id,
        matched_label=matched_label,
        catalog_reference_key=catalog_reference_key,
        match_method=match_method,
        confidence=confidence,
        status=status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
