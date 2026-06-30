from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
REPORT_ROOT = ROOT / "reports" / "migration_v2"
DEFAULT_CONTRACT = BACKEND / "app" / "migration_v2" / "contracts" / "datagalaxy_athena_v1.yaml"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def setup_logging(name: str) -> logging.Logger:
    logging.basicConfig(
        level=os.getenv("MIGRATION_V2_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger(name)


def postgres_url() -> str:
    value = os.getenv("POSTGRES_URL")
    if not value:
        raise SystemExit("POSTGRES_URL is required. Export it before running migration_v2 scripts.")
    return value


def postgres_engine() -> Engine:
    return create_engine(postgres_url(), pool_pre_ping=True)


def postgres_engine_from_url(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def load_env_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise SystemExit(f"Environment config not found: {config_path}")
    text_value = config_path.read_text(encoding="utf-8")
    try:
        return json.loads(text_value)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise SystemExit(
                f"Environment config {config_path} is not JSON-shaped YAML and PyYAML is not installed."
            ) from exc
        return yaml.safe_load(text_value)


def config_section(config: dict[str, Any], section: str) -> dict[str, Any]:
    value = config.get(section)
    if not isinstance(value, dict):
        raise SystemExit(f"Environment config is missing section: {section}")
    return value


def ensure_tables(engine: Engine, table_names: Iterable[str]) -> None:
    expected = {name.lower() for name in table_names}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                """
            )
        ).scalars()
        existing = {str(row).lower() for row in rows}
    missing = sorted(expected - existing)
    if missing:
        migration = ROOT / "backend" / "migrations" / "sql" / "010_migration_v2_staging.sql"
        raise SystemExit(
            "Missing migration_v2 tables: "
            + ", ".join(missing)
            + f". Apply {migration} before continuing."
        )


def report_dir(export_id: str) -> Path:
    path = REPORT_ROOT / export_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if pd.isna(value) if not isinstance(value, (dict, list, tuple, set)) else False:
        return None
    return str(value)


def write_json_report(export_id: str, filename: str, payload: dict[str, Any]) -> Path:
    path = report_dir(export_id) / filename
    path.write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")
    return path


def write_markdown_report(export_id: str, filename: str, title: str, sections: list[tuple[str, str]]) -> Path:
    lines = [f"# {title}", ""]
    for heading, body in sections:
        lines.extend([f"## {heading}", "", body.strip(), ""])
    path = report_dir(export_id) / filename
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def load_contract(path: str | Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    contract_path = Path(path)
    if not contract_path.exists():
        raise SystemExit(f"Contract not found: {contract_path}")
    text_value = contract_path.read_text(encoding="utf-8")
    try:
        return json.loads(text_value)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise SystemExit(
                f"Contract {contract_path} is not JSON-shaped YAML and PyYAML is not installed."
            ) from exc
        return yaml.safe_load(text_value)


def table_contracts(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return dict(contract.get("tables") or {})


def known_raw_tables(contract: dict[str, Any]) -> set[str]:
    return set(table_contracts(contract).keys())


def infer_raw_table_name(path: Path, known_tables: set[str]) -> str:
    stem = path.stem.lower()
    for table in sorted(known_tables, key=len, reverse=True):
        if table.lower() in stem:
            return table
    return stem


def discover_raw_files(export_path: Path, known_tables: set[str]) -> list[dict[str, Any]]:
    if not export_path.exists():
        raise SystemExit(f"Export path does not exist: {export_path}")
    if not export_path.is_dir():
        raise SystemExit(f"Export path must be a directory: {export_path}")

    files: list[dict[str, Any]] = []
    for path in sorted(export_path.rglob("*")):
        if path.suffix.lower() not in {".csv", ".tsv", ".parquet"}:
            continue
        files.append(
            {
                "path": path,
                "raw_table_name": infer_raw_table_name(path, known_tables),
                "detected_format": path.suffix.lower().lstrip("."),
            }
        )
    if not files:
        raise SystemExit(f"No CSV, TSV, or Parquet files found under {export_path}")
    return files


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_frame(path: Path, nrows: int | None = None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        frame = pd.read_parquet(path)
        return frame.head(nrows) if nrows else frame
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t", dtype=str, nrows=nrows, keep_default_na=False)
    try:
        return pd.read_csv(path, sep=None, engine="python", dtype=str, nrows=nrows, keep_default_na=False)
    except UnicodeDecodeError:
        return pd.read_csv(
            path,
            sep=None,
            engine="python",
            dtype=str,
            nrows=nrows,
            keep_default_na=False,
            encoding="latin1",
        )


def clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def clean_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: clean_value(value) for key, value in record.items()}


def json_param(value: Any) -> str:
    return json.dumps(value, default=json_default)


def path_hash(path_full: str | None) -> str | None:
    if not path_full:
        return None
    return hashlib.sha256(path_full.encode("utf-8")).hexdigest()


def fetch_export_path(engine: Engine, export_id: str) -> Path:
    ensure_tables(engine, ["migration_export_run"])
    with engine.connect() as conn:
        export_path = conn.execute(
            text("SELECT export_path FROM migration_export_run WHERE export_id = :export_id"),
            {"export_id": export_id},
        ).scalar()
    if not export_path:
        raise SystemExit(f"Export {export_id!r} is not registered or has no export_path.")
    return Path(str(export_path))


def fetch_raw_files(engine: Engine, export_id: str) -> list[dict[str, Any]]:
    ensure_tables(engine, ["migration_raw_file"])
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT raw_table_name, file_path, detected_format
                FROM migration_raw_file
                WHERE export_id = :export_id
                ORDER BY raw_table_name, file_path
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
    files = [dict(row) for row in rows]
    if not files:
        raise SystemExit(f"No raw files are registered for export {export_id!r}.")
    return files


def replace_export_rows(engine: Engine, table_name: str, export_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {table_name} WHERE export_id = :export_id"), {"export_id": export_id})
