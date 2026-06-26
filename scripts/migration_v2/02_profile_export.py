from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from sqlalchemy import text

from _common import (
    clean_value,
    ensure_tables,
    fetch_raw_files,
    json_param,
    postgres_engine,
    read_frame,
    setup_logging,
    write_json_report,
    write_markdown_report,
)


LOGGER = setup_logging("migration_v2.profile_export")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile registered raw DataGalaxy export files.")
    parser.add_argument("--export-id", required=True, help="Export identifier registered by 01_register_export.py.")
    parser.add_argument("--sample-size", type=int, default=10, help="Distinct sample values to keep per column.")
    return parser.parse_args()


def guess_type(values: list[Any]) -> str:
    non_null = [value for value in values if value is not None]
    if not non_null:
        return "empty"
    lowered = {str(value).strip().lower() for value in non_null}
    boolean_tokens = {"true", "false", "1", "0", "yes", "no", "y", "n", "oui", "non"}
    if lowered <= boolean_tokens:
        return "boolean"
    numeric = 0
    for value in non_null:
        try:
            float(str(value))
            numeric += 1
        except ValueError:
            pass
    if numeric == len(non_null):
        return "numeric"
    return "text"


def profile_column(series, sample_size: int) -> dict[str, Any]:
    cleaned = [clean_value(value) for value in series.tolist()]
    non_null = [value for value in cleaned if value is not None]
    sample_values = []
    seen = set()
    for value in non_null:
        marker = str(value)
        if marker in seen:
            continue
        seen.add(marker)
        sample_values.append(value)
        if len(sample_values) >= sample_size:
            break
    warnings = []
    if series.name == "v_ident_works" and len({str(value) for value in non_null}) <= 1:
        warnings.append("workspace_column_is_constant_do_not_join_entities_on_it")
    return {
        "data_type_guess": guess_type(sample_values or non_null[:sample_size]),
        "null_count": len(cleaned) - len(non_null),
        "non_null_count": len(non_null),
        "distinct_count": len({str(value) for value in non_null}),
        "sample_values": sample_values,
        "warnings": warnings,
    }


def main() -> None:
    args = parse_args()
    engine = postgres_engine()
    ensure_tables(engine, ["migration_raw_file", "migration_column_profile"])
    raw_files = fetch_raw_files(engine, args.export_id)

    LOGGER.info("Profiling export_id=%s files=%s", args.export_id, len(raw_files))
    profiles: list[dict[str, Any]] = []
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM migration_column_profile WHERE export_id = :export_id"),
            {"export_id": args.export_id},
        )
        for raw_file in raw_files:
            path = Path(raw_file["file_path"])
            if not path.exists():
                raise SystemExit(f"Registered raw file is missing: {path}")
            frame = read_frame(path)
            for column in frame.columns:
                profile = profile_column(frame[column], args.sample_size)
                row = {
                    "export_id": args.export_id,
                    "raw_table_name": raw_file["raw_table_name"],
                    "column_name": str(column),
                    **profile,
                }
                profiles.append(row)
                conn.execute(
                    text(
                        """
                        INSERT INTO migration_column_profile(
                            export_id, raw_table_name, column_name, data_type_guess,
                            null_count, distinct_count, non_null_count, sample_values, warnings
                        )
                        VALUES (
                            :export_id, :raw_table_name, :column_name, :data_type_guess,
                            :null_count, :distinct_count, :non_null_count,
                            CAST(:sample_values AS jsonb), CAST(:warnings AS jsonb)
                        )
                        """
                    ),
                    {
                        **row,
                        "sample_values": json_param(row["sample_values"]),
                        "warnings": json_param(row["warnings"]),
                    },
                )

    by_table: dict[str, int] = {}
    for profile in profiles:
        by_table[profile["raw_table_name"]] = by_table.get(profile["raw_table_name"], 0) + 1
    payload = {
        "export_id": args.export_id,
        "column_profile_count": len(profiles),
        "columns_by_table": by_table,
        "profiles": profiles,
    }
    json_path = write_json_report(args.export_id, "profile_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "profile_report.md",
        "Migration V2 Profile Report",
        [
            ("Summary", f"Profiled `{len(profiles)}` columns across `{len(by_table)}` raw tables."),
            ("Columns By Table", "\n".join(f"- `{table}`: {count}" for table, count in sorted(by_table.items()))),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
