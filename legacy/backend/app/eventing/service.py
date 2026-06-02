from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.eventing.config import ENABLE_EVENT_CATALOG_RESOLUTION, ENABLE_EVENT_KG_WRITES
from backend.app.eventing.event_kg_writer import EventKGWriter
from backend.app.eventing.normalizer import normalize_dataquality_event, normalize_pipeline_event
from backend.app.eventing.repositories import (
    save_dataquality_result,
    save_event_store,
    save_pipeline_run,
    save_resolution,
)
from backend.app.eventing.resolver import resolve_dataquality_event, resolve_pipeline_event
from backend.app.eventing.validator import validate_dataquality_event, validate_pipeline_event


def _unresolved_reference() -> dict:
    return {
        "matched_node_id": None,
        "matched_label": None,
        "catalog_reference_key": None,
        "match_method": None,
        "confidence": 0.0,
        "status": "RESOLUTION_DISABLED",
    }


def process_dataquality_event(db: Session, topic: str, event: dict) -> dict:
    validate_dataquality_event(event)
    normalized = normalize_dataquality_event(event)

    event_store = save_event_store(db, normalized, event)
    dq_row = save_dataquality_result(db, raw_event_id=event_store.id, dq_result=normalized["dq_result"])

    resolution = (
        resolve_dataquality_event(db, normalized["dq_result"])
        if ENABLE_EVENT_CATALOG_RESOLUTION
        else _unresolved_reference()
    )

    save_resolution(
        db,
        event_store_id=event_store.id,
        event_family="data_quality",
        matched_node_id=resolution.get("matched_node_id"),
        matched_label=resolution.get("matched_label"),
        catalog_reference_key=resolution.get("catalog_reference_key"),
        match_method=resolution.get("match_method"),
        confidence=resolution.get("confidence"),
        status=resolution.get("status"),
    )

    if ENABLE_EVENT_KG_WRITES:
        writer = EventKGWriter()
        try:
            writer.write_dataquality_result(
                topic=topic,
                event_store_id=event_store.id,
                dq_result_id=dq_row.id,
                normalized=normalized,
                resolution=resolution,
            )
        finally:
            writer.close()

    return {"event_store_id": event_store.id, "dq_result_id": dq_row.id, "resolution": resolution}


def process_pipeline_event(db: Session, topic: str, event: dict) -> dict:
    validate_pipeline_event(event)
    normalized = normalize_pipeline_event(event)

    event_store = save_event_store(db, normalized, event)
    run_row = save_pipeline_run(db, raw_event_id=event_store.id, pipeline_run=normalized["pipeline_run"])

    resolution = (
        resolve_pipeline_event(db, normalized["pipeline_run"])
        if ENABLE_EVENT_CATALOG_RESOLUTION
        else _unresolved_reference()
    )

    save_resolution(
        db,
        event_store_id=event_store.id,
        event_family="pipeline_execution",
        matched_node_id=resolution.get("matched_node_id"),
        matched_label=resolution.get("matched_label"),
        catalog_reference_key=resolution.get("catalog_reference_key"),
        match_method=resolution.get("match_method"),
        confidence=resolution.get("confidence"),
        status=resolution.get("status"),
    )

    if ENABLE_EVENT_KG_WRITES:
        writer = EventKGWriter()
        try:
            writer.write_pipeline_run(
                topic=topic,
                event_store_id=event_store.id,
                pipeline_run_id=run_row.id,
                normalized=normalized,
                resolution=resolution,
            )
        finally:
            writer.close()

    return {"event_store_id": event_store.id, "pipeline_run_id": run_row.id, "resolution": resolution}
