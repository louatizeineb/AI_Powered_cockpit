from __future__ import annotations
from dataclasses import dataclass
from typing import Any

REQUIRED_FIELDS = [
    "applicationcode",
    "controlledobjectname",
    "controlleditemcount",
    "okcount",
    "kocount",
]


@dataclass
class ValidationResult:
    valid: bool
    reason: str | None = None
    details: dict[str, Any] | None = None


def _is_missing(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def validate_schema(event: dict) -> ValidationResult:
    missing = [f for f in REQUIRED_FIELDS if _is_missing(event.get(f))]
    if missing:
        return ValidationResult(False, "MISSING_DQC_CRITICAL_DATA", {"missing_fields": missing})
    return ValidationResult(True)


def _to_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def validate_counts(event: dict) -> ValidationResult:
    total = _to_int(event.get("controlleditemcount"))
    ok = _to_int(event.get("okcount"))
    ko = _to_int(event.get("kocount"))
    if total is None or ok is None or ko is None:
        return ValidationResult(False, "INVALID_COUNT_FIELDS", {"total": total, "ok": ok, "ko": ko})
    if total != ok + ko:
        return ValidationResult(False, "COUNT_INCONSISTENCY", {"controlleditemcount": total, "okcount": ok, "kocount": ko, "expected_total": ok + ko})
    return ValidationResult(True)
