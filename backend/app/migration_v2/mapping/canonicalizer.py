from __future__ import annotations

from typing import Any


def canonicalize_record(raw_record: dict[str, Any], column_map: dict[str, str]) -> dict[str, Any]:
    """Convert one raw record to canonical field names using a contract column map."""

    return {canonical_field: raw_record.get(raw_column) for canonical_field, raw_column in column_map.items()}
