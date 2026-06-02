from __future__ import annotations
from datetime import datetime
from typing import Any
from app.dqc.resolution.parser import parse_controlled_object


def _to_int(v: Any) -> int | None:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return None


def _to_float(v: Any) -> float | None:
    try:
        return float(str(v).replace(',', '.').strip())
    except Exception:
        return None


def normalize_event(event: dict, source_system: str = "unknown") -> dict:
    parsed = parse_controlled_object(
        event.get("applicationcode"),
        event.get("controlledobjectname"),
        event.get("controlledsourcename"),
    )
    total = _to_int(event.get("controlleditemcount")) or 0
    ok = _to_int(event.get("okcount")) or 0
    ko = _to_int(event.get("kocount")) or 0
    ko_rate = (ko / total) if total else None
    quality_score = (ok / total * 100.0) if total else None

    return {
        "raw_dqc_id": str(event.get("id")) if event.get("id") is not None else None,
        "source_system": source_system,
        "application_code_raw": event.get("applicationcode"),
        "controlled_object_name_raw": event.get("controlledobjectname"),
        "controlled_source_name_raw": event.get("controlledsourcename"),
        **parsed,
        "quality_dimension": event.get("qualitydimension"),
        "control_name": event.get("controlname"),
        "control_tool": event.get("controltool"),
        "cdq_profile": event.get("cdqprofile"),
        "control_link": event.get("controllink"),
        "acceptance_threshold": _to_float(event.get("acceptancethreshold")),
        "controlled_item_count": total,
        "ok_count": ok,
        "ko_count": ko,
        "ko_rate": ko_rate,
        "quality_score": quality_score,
        "raw_payload": event,
        "normalized_at": datetime.utcnow().isoformat(),
    }
