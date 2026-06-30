from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from _common import (
    clean_value,
    discover_raw_files,
    ensure_tables,
    json_param,
    load_contract,
    postgres_engine,
    read_frame,
    setup_logging,
    sha256_file,
    table_contracts,
    write_json_report,
    write_markdown_report,
)


LOGGER = setup_logging("migration_v2.register_export")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register a raw DataGalaxy export for migration_v2.")
    parser.add_argument("--export-id", required=True, help="Stable export identifier, for example dg_2026_new.")
    parser.add_argument("--export-path", required=True, help="Directory containing raw CSV, TSV, or Parquet files.")
    parser.add_argument(
        "--contract",
        default=None,
        help="Mapping contract path. Defaults to backend/app/migration_v2/contracts/datagalaxy_athena_v1.yaml.",
    )
    parser.add_argument(
        "--skip-row-count",
        action="store_true",
        help="Register files without reading them for row and column counts.",
    )
    return parser.parse_args()


def file_summary(path: Path, skip_row_count: bool) -> dict[str, object]:
    summary: dict[str, object] = {
        "file_path": str(path),
        "file_hash": sha256_file(path),
        "detected_format": path.suffix.lower().lstrip("."),
        "row_count": None,
        "column_count": None,
        "columns": [],
    }
    if skip_row_count:
        return summary

    frame = read_frame(path)
    summary["row_count"] = int(len(frame.index))
    summary["column_count"] = int(len(frame.columns))
    summary["columns"] = [str(column) for column in frame.columns]
    return summary


def main() -> None:
    args = parse_args()
    contract = load_contract(args.contract) if args.contract else load_contract()
    contract_version = str(contract.get("contract_version") or "unknown")
    known_tables = set(table_contracts(contract).keys())
    export_path = Path(args.export_path)
    raw_files = discover_raw_files(export_path, known_tables)

    engine = postgres_engine()
    ensure_tables(engine, ["migration_export_run", "migration_raw_file"])

    LOGGER.info("Registering export_id=%s path=%s", args.export_id, export_path)
    registered_files: list[dict[str, object]] = []
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO migration_export_run(export_id, export_path, contract_version, status, metadata)
                VALUES (:export_id, :export_path, :contract_version, 'registered', CAST(:metadata AS jsonb))
                ON CONFLICT (export_id) DO UPDATE
                SET export_path = EXCLUDED.export_path,
                    contract_version = EXCLUDED.contract_version,
                    status = 'registered',
                    metadata = EXCLUDED.metadata
                """
            ),
            {
                "export_id": args.export_id,
                "export_path": str(export_path),
                "contract_version": contract_version,
                "metadata": json_param(
                    {
                        "registered_at": datetime.now(timezone.utc).isoformat(),
                        "contract_name": clean_value(contract.get("contract_name")),
                    }
                ),
            },
        )
        conn.execute(
            text("DELETE FROM migration_raw_file WHERE export_id = :export_id"),
            {"export_id": args.export_id},
        )

        for item in raw_files:
            path = Path(item["path"])
            summary = file_summary(path, args.skip_row_count)
            summary["raw_table_name"] = item["raw_table_name"]
            registered_files.append(summary)
            conn.execute(
                text(
                    """
                    INSERT INTO migration_raw_file(
                        export_id, raw_table_name, file_path, file_hash, row_count,
                        column_count, detected_format, columns
                    )
                    VALUES (
                        :export_id, :raw_table_name, :file_path, :file_hash, :row_count,
                        :column_count, :detected_format, CAST(:columns AS jsonb)
                    )
                    """
                ),
                {
                    "export_id": args.export_id,
                    "raw_table_name": item["raw_table_name"],
                    "file_path": str(path),
                    "file_hash": summary["file_hash"],
                    "row_count": summary["row_count"],
                    "column_count": summary["column_count"],
                    "detected_format": summary["detected_format"],
                    "columns": json_param(summary["columns"]),
                },
            )

    payload = {
        "export_id": args.export_id,
        "export_path": str(export_path),
        "contract_version": contract_version,
        "file_count": len(registered_files),
        "files": registered_files,
    }
    json_path = write_json_report(args.export_id, "registration_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "registration_report.md",
        "Migration V2 Registration Report",
        [
            ("Summary", f"Registered `{args.export_id}` with `{len(registered_files)}` raw files."),
            (
                "Files",
                "\n".join(
                    f"- `{item['raw_table_name']}`: `{item['file_path']}` rows={item['row_count']}"
                    for item in registered_files
                ),
            ),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
