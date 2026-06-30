from __future__ import annotations

from typing import Any

from app.dqc.config import DQC_ENVIRONMENT
from app.dqc.observability.dlq_log_formatter import format_dlq_log
from app.dqc.observability.log_events import write_dlq_log


def emit_dlq_observability_log(
    *,
    dlq_id: int | None,
    topic: str,
    payload: dict[str, Any],
    error_type: str,
    error_message: str,
) -> None:
    document = format_dlq_log(
        dlq_id=dlq_id,
        topic=topic,
        payload=payload,
        error_type=error_type,
        error_message=error_message,
        environment=DQC_ENVIRONMENT,
    )
    write_dlq_log(document)
