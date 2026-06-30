from __future__ import annotations

from typing import Any


def exact_mapping_decisions(table_contract: dict[str, Any], columns: set[str]) -> list[dict[str, Any]]:
    """Generate exact-match mapping decisions for a raw table."""

    decisions = []
    for canonical_field, raw_column in (table_contract.get("columns") or {}).items():
        matched = raw_column in columns
        decisions.append(
            {
                "canonical_field": canonical_field,
                "raw_column_name": raw_column,
                "decision_type": "auto_exact_match" if matched else "missing_column",
                "requires_human_approval": not matched,
            }
        )
    return decisions
