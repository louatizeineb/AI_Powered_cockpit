from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.dqc.business_rules import validate_dataquality_business_rules
from backend.app.dqc.config import ENABLE_DQC_CATALOG_RESOLUTION, ENABLE_DQC_EVENT_KG_WRITES
from backend.app.dqc.event_kg_writer import DQCEventKGWriter
from backend.app.dqc.normalizer import normalize_dataquality_event
from backend.app.dqc.repositories import save_dqc_event_store, save_dqc_resolution, save_dqc_result
from backend.app.dqc.resolver import resolve_dataquality_event
from backend.app.dqc.validator import validate_dataquality_event


def _resolution_disabled() -> dict:
    return {
        "matched_node_id": None,
        "matched_label": None,
        "catalog_reference_key": None,
        "match_method": None,
        "confidence": 0.0,
        "status": "RESOLUTION_DISABLED",
    }


def process_dqc_event(db: Session, topic: str, event: dict) -> dict:
    validate_dataquality_event(event)
    normalized = normalize_dataquality_event(event)
    validate_dataquality_business_rules(normalized["dq_result"])

    try:
        event_store = save_dqc_event_store(db, normalized, event)
        dq_row = save_dqc_result(db, raw_event_id=event_store.id, dq_result=normalized["dq_result"])

        resolution = (
            resolve_dataquality_event(db, normalized["dq_result"])
            if ENABLE_DQC_CATALOG_RESOLUTION
            else _resolution_disabled()
        )

        save_dqc_resolution(
            db,
            event_store_id=event_store.id,
            dq_result_id=dq_row.id,
            matched_node_id=resolution.get("matched_node_id"),
            matched_label=resolution.get("matched_label"),
            catalog_reference_key=resolution.get("catalog_reference_key"),
            match_method=resolution.get("match_method"),
            confidence=resolution.get("confidence"),
            status=resolution.get("status"),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    if ENABLE_DQC_EVENT_KG_WRITES:
        writer = DQCEventKGWriter()
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
