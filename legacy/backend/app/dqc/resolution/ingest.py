from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

SUPPORTED_DQC_EXTENSIONS = {".csv", ".json", ".jsonl", ".parquet", ".pq"}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "") for c in df.columns]
    return df.astype(object).where(pd.notnull(df), None)


def _read_csv(path: Path) -> pd.DataFrame:
    """
    Robust CSV reader for exports from CDQ/DQC tools.
    Handles comma, semicolon, tab, BOM, and all-string values.
    """
    last_error: Exception | None = None
    encodings = ["utf-8-sig", "utf-8", "latin1"]
    for encoding in encodings:
        try:
            return pd.read_csv(
                path,
                dtype=str,
                sep=None,          # autodetect comma/semicolon/tab
                engine="python",
                encoding=encoding,
            )
        except Exception as exc:  # try next encoding
            last_error = exc
    raise ValueError(f"Could not read CSV file '{path.name}': {last_error}")


def _read_json(path: Path) -> pd.DataFrame:
    try:
        # Try standard JSON first: list[dict] or object.
        with path.open("r", encoding="utf-8-sig") as f:
            obj = json.load(f)
        if isinstance(obj, list):
            return pd.DataFrame(obj)
        if isinstance(obj, dict):
            if isinstance(obj.get("items"), list):
                return pd.DataFrame(obj["items"])
            if isinstance(obj.get("data"), list):
                return pd.DataFrame(obj["data"])
            return pd.DataFrame([obj])
        raise ValueError("JSON root must be an object or list of objects")
    except json.JSONDecodeError:
        # Some .json files are actually JSONL.
        return pd.read_json(path, lines=True)


def read_dqc_file(path: str | Path) -> list[dict]:
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix not in SUPPORTED_DQC_EXTENSIONS:
        raise ValueError(
            f"Unsupported DQC file type '{suffix}'. Supported: CSV, JSON, JSONL, Parquet, PQ."
        )

    if suffix == ".csv":
        df = _read_csv(path)
    elif suffix == ".jsonl":
        df = pd.read_json(path, lines=True)
    elif suffix == ".json":
        df = _read_json(path)
    elif suffix in {".parquet", ".pq"}:
        df = pd.read_parquet(path)
    else:
        raise ValueError(f"Unsupported DQC file type: {suffix}")

    df = _normalize_columns(df)
    if df.empty:
        raise ValueError(f"Uploaded DQC file '{path.name}' contains no rows")

    return df.to_dict(orient="records")
