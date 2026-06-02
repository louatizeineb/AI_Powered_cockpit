from __future__ import annotations

from uuid import uuid4


class EventNormalizationError(Exception):
    """Raised when an event cannot be safely normalized."""


def _required_int(value, field_name: str) -> int:
    if value is None or str(value).strip() == "":
        raise EventNormalizationError(f"Missing required integer field: {field_name}")
    try:
        return int(value)
    except Exception as exc:
        raise EventNormalizationError(f"Invalid integer field {field_name}: {value}") from exc


def _to_float(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except Exception as exc:
        raise EventNormalizationError(f"Invalid float value: {value}") from exc


def derive_quality_status(computed_score: float | None, acceptance_threshold: float | None) -> str:
    if computed_score is None:
        return "UNKNOWN"
    if acceptance_threshold is not None:
        return "PASSED" if computed_score >= acceptance_threshold else "FAILED"
    if computed_score >= 80:
        return "GOOD"
    if computed_score >= 50:
        return "WARNING"
    return "CRITICAL"


def normalize_dataquality_event(event: dict) -> dict:
    try:
        entity = event["payload"]["entity"]
        data = entity["data"]
    except Exception as exc:
        raise EventNormalizationError("Invalid DQC envelope: expected payload.entity.data") from exc

    controlled_item_count = _required_int(data.get("controlledItemCount"), "controlledItemCount")
    ok_count = _required_int(data.get("okCount"), "okCount")
    ko_count = _required_int(data.get("koCount"), "koCount")
    acceptance_threshold = _to_float(data.get("acceptanceThreshold"))

    computed_score = round((ok_count / controlled_item_count) * 100, 2) if controlled_item_count else None
    quality_status = derive_quality_status(computed_score, acceptance_threshold)
    id_ref = entity.get("idRef") or event.get("metadata", {}).get("eventId") or f"dq-{uuid4()}"

    return {
        "event_id": id_ref,
        "event_family": "data_quality",
        "event_type": "DataQualityCheckResult",
        "schema_id": "https://schema.local/dqc/dataqualitycheckresult-v2",
        "schema_version": "v2",
        "source_system": "DQC",
        "correlation_id": id_ref,
        "dq_result": {
            "id_ref": id_ref,
            "application_code": data.get("applicationCode"),
            "controlled_object_name": data.get("controlledObjectName"),
            "controlled_object_type": data.get("controlledObjectType"),
            "controlled_source_name": data.get("controlledSourceName"),
            "business_term_name": data.get("businessTermName"),
            "control_name": data.get("controlName"),
            "quality_dimension": data.get("qualityDimension"),
            "acceptance_threshold": acceptance_threshold,
            "execution_timestamp": data.get("executionTimestamp"),
            "business_date": data.get("businessDate"),
            "controlled_item_count": controlled_item_count,
            "ok_count": ok_count,
            "ko_count": ko_count,
            "control_tool": data.get("controlTool"),
            "computed_score": computed_score,
            "quality_status": quality_status,
        },
    }
