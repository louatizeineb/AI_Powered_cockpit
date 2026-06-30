from __future__ import annotations

from typing import Any


def clean_scalar(value: Any) -> Any:
    """Normalize empty strings to null and trim text."""

    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value
