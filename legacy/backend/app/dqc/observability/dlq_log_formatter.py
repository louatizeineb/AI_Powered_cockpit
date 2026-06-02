from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def format_dlq_log(
    *,
    dlq_id: int | None,
    topic: str,
    payload: dict[str, Any],
    error_type: str,
    error_message: str,
    environment: str = "test",
) -> dict[str, Any]:
    event_id = None
    app_code = None
    controlled_object_name = None
    controlled_object_type = None
    control_name = None

    try:
        entity = payload.get("payload", {}).get("entity", {})
        data = entity.get("data", {})
        event_id = entity.get("idRef") or payload.get("metadata", {}).get("eventId")
        app_code = data.get("applicationCode")
        controlled_object_name = data.get("controlledObjectName")
        controlled_object_type = data.get("controlledObjectType")
        control_name = data.get("controlName")
    except Exception:
        pass

    return {
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "log_type": "dqc_dlq",
        "environment": environment,
        "dlq_id": dlq_id,
        "topic": topic,
        "event_id": event_id,
        "application_code": app_code,
        "controlled_object_name": controlled_object_name,
        "controlled_object_type": controlled_object_type,
        "control_name": control_name,
        "error_type": error_type,
        "error_message": error_message,
        "payload_preview": str(payload)[:3000],
    }
