from __future__ import annotations

from typing import Any


def detect_schema_drift(contract: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Compare a contract with a profile payload without writing database state."""

    contract_tables = contract.get("tables") or {}
    profile_tables = _profile_tables(profile)
    table_results: dict[str, Any] = {}
    for raw_table_name, table_contract in contract_tables.items():
        expected = set((table_contract.get("columns") or {}).values())
        required = set(table_contract.get("required_columns") or [])
        actual = profile_tables.get(raw_table_name, set())
        table_results[raw_table_name] = {
            "missing_required_columns": sorted(required - actual),
            "missing_mapped_columns": sorted(expected - actual),
            "unexpected_columns": sorted(actual - expected),
            "matched_columns": sorted(actual & expected),
        }
    missing_required = [
        {"raw_table_name": table, "column_name": column}
        for table, result in table_results.items()
        for column in result["missing_required_columns"]
    ]
    return {
        "contract_version": contract.get("contract_version"),
        "status": "blocked" if missing_required else "ready",
        "missing_tables": sorted(set(contract_tables) - set(profile_tables)),
        "unexpected_tables": sorted(set(profile_tables) - set(contract_tables)),
        "missing_required_columns": missing_required,
        "tables": table_results,
    }


def _profile_tables(profile: dict[str, Any]) -> dict[str, set[str]]:
    if "tables" in profile and isinstance(profile["tables"], dict):
        return {
            str(table): {str(column) for column in columns}
            for table, columns in profile["tables"].items()
        }
    if "columns" in profile:
        table_name = str(profile.get("raw_table_name") or profile.get("table") or "raw_export")
        return {table_name: {str(row.get("column_name")) for row in profile.get("columns") or []}}
    result: dict[str, set[str]] = {}
    for row in profile.get("profiles") or profile.get("rows") or []:
        table = str(row.get("raw_table_name") or row.get("table") or "raw_export")
        result.setdefault(table, set()).add(str(row.get("column_name")))
    return result
