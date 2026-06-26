from __future__ import annotations

from datetime import datetime


def parse_datetime_or_none(value: str | None, formats: list[str]) -> datetime | None:
    """Parse a datetime with explicit contract formats."""

    if not value:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
