from __future__ import annotations

from uuid import uuid4


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _to_float(value):
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def derive_quality_status(
    computed_score: float | None,
    acceptance_threshold: float | None,
) -> str:
    if computed_score is None:
        return "UNKNOWN"

    # Prefer the business threshold from the event.
    if acceptance_threshold is not None:
        return "PASSED" if computed_score >= acceptance_threshold else "FAILED"

    # Fallback only when no threshold is provided.
    if computed_score >= 80:
        return "GOOD"
    if computed_score >= 50:
        return "WARNING"
    return "CRITICAL"


def derive_pipeline_severity(status: str | None, event_status: str | None, incoming_severity: str | None) -> str:
    if incoming_severity:
        return incoming_severity

    value = (status or event_status or "").lower()

    if value in {"failed", "failure", "error", "ko"}:
        return "error"
    if value in {"warning", "partial_success", "partial"}:
        return "warning"
    if value in {"success", "completed", "complete", "ok"}:
        return "info"

    return "unknown"


def normalize_dataquality_event(event: dict) -> dict:
    entity = event["payload"]["entity"]
    data = entity["data"]

    controlled_item_count = _to_int(data.get("controlledItemCount"))
    ok_count = _to_int(data.get("okCount"))
    ko_count = _to_int(data.get("koCount"))

    acceptance_threshold = _to_float(data.get("acceptanceThreshold"))

    computed_score = (
        round((ok_count / controlled_item_count) * 100, 2)
        if controlled_item_count
        else None
    )

    quality_status = derive_quality_status(
        computed_score=computed_score,
        acceptance_threshold=acceptance_threshold,
    )

    id_ref = entity.get("idRef") or f"dq-{uuid4()}"

    return {
        "event_id": id_ref,
        "event_family": "data_quality",
        "event_type": "DataQualityCheckResult",
        "schema_id": "https://schema.event.bpifrance.fr/dag/dataqualitycheckresult-v2",
        "schema_version": "v2",
        "source_system": "DAG",
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


def normalize_pipeline_event(event: dict) -> dict:
    headers = event["headers"]
    payload = event["payload"]
    metadata = event["metadata"]
    source = payload.get("source") or {}

    correlation_id = headers.get("correlationId") or f"pipeline-{uuid4()}"

    status = payload.get("status")
    event_status = headers.get("eventStatus")
    severity = derive_pipeline_severity(
        status=status,
        event_status=event_status,
        incoming_severity=metadata.get("severity"),
    )

    return {
        "event_id": correlation_id,
        "event_family": "pipeline_execution",
        "event_type": headers.get("eventType"),
        "schema_id": "https://schema.event.bpifrance.fr/xyz/notificationEvent",
        "schema_version": metadata.get("version") or "v1",
        "source_system": headers.get("pipelineType"),
        "correlation_id": correlation_id,
        "pipeline_run": {
            "correlation_id": correlation_id,
            "pipeline_type": headers.get("pipelineType"),
            "event_status": event_status,
            "event_type": headers.get("eventType"),
            "pipeline_name": payload.get("pipelineName"),
            "status": status,
            "start_time": payload.get("startTime"),
            "end_time": payload.get("endTime"),
            "duration": payload.get("duration"),
            "source_database": source.get("database"),
            "source_table": source.get("table"),
            "environment_name": metadata.get("environment"),
            "severity": severity,
        },
    }