from __future__ import annotations


def gate_required(reason: str) -> dict[str, str]:
    """Represent a human approval gate needed before the next deterministic action."""

    return {"status": "gate_required", "reason": reason}
