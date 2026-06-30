from __future__ import annotations

from typing import Any


def normalize_bool(value: Any, true_values: set[str], false_values: set[str]) -> bool | None:
    """Normalize common DataGalaxy boolean tokens."""

    if value is None:
        return None
    token = str(value).strip().lower()
    if token in true_values:
        return True
    if token in false_values:
        return False
    return None
