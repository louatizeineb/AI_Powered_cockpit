from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _read_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)
    if suffix in {".json", ".jsonl", ".ndjson"}:
        return pd.read_json(path, lines=suffix in {".jsonl", ".ndjson"})
    raise ValueError(f"Unsupported raw export file type: {suffix or '<none>'}")


def _clean(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text if text else None


def _guess_type(values: list[Any]) -> str:
    non_null = [value for value in values if value is not None]
    if not non_null:
        return "empty"
    lowered = {str(value).strip().lower() for value in non_null}
    if lowered <= {"true", "false", "1", "0", "yes", "no", "y", "n", "oui", "non"}:
        return "boolean"
    numeric = 0
    for value in non_null:
        try:
            float(str(value))
            numeric += 1
        except ValueError:
            pass
    return "numeric" if numeric == len(non_null) else "text"


def profile_file(path: Path, *, sample_size: int = 10) -> dict[str, Any]:
    """Profile one raw export file without writing database state."""

    frame = _read_file(path)
    columns: list[dict[str, Any]] = []
    for column_name in frame.columns:
        values = [_clean(value) for value in frame[column_name].tolist()]
        non_null = [value for value in values if value is not None]
        samples = []
        seen = set()
        for value in non_null:
            marker = str(value)
            if marker in seen:
                continue
            seen.add(marker)
            samples.append(value)
            if len(samples) >= sample_size:
                break
        columns.append(
            {
                "column_name": str(column_name),
                "data_type_guess": _guess_type(samples or non_null[:sample_size]),
                "null_count": len(values) - len(non_null),
                "non_null_count": len(non_null),
                "distinct_count": len({str(value) for value in non_null}),
                "sample_values": samples,
                "warnings": (
                    ["workspace_column_is_constant_do_not_join_entities_on_it"]
                    if str(column_name) == "v_ident_works" and len({str(value) for value in non_null}) <= 1
                    else []
                ),
            }
        )
    return {
        "path": str(path),
        "status": "profiled",
        "row_count": len(frame),
        "column_count": len(columns),
        "columns": columns,
    }
