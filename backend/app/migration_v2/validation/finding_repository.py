from __future__ import annotations

from typing import Any


def finding(severity: str, category: str, message: str, **evidence: Any) -> dict[str, Any]:
    """Build a validation finding payload."""

    return {"severity": severity, "category": category, "message": message, "evidence": evidence}
