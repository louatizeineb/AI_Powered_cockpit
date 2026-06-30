from __future__ import annotations

import argparse
from collections import defaultdict
from typing import Any

from sqlalchemy import text

from _common import (
    ensure_tables,
    json_param,
    load_contract,
    postgres_engine,
    setup_logging,
    table_contracts,
    write_json_report,
    write_markdown_report,
)


LOGGER = setup_logging("migration_v2.detect_schema_drift")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect schema drift between raw profiles and a mapping contract.")
    parser.add_argument("--export-id", required=True, help="Export identifier registered by 01_register_export.py.")
    parser.add_argument("--contract", required=True, help="Path to the migration_v2 mapping contract.")
    return parser.parse_args()


def fetch_profiles(engine, export_id: str) -> dict[str, set[str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT raw_table_name, column_name
                FROM migration_column_profile
                WHERE export_id = :export_id
                ORDER BY raw_table_name, column_name
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
    if not rows:
        raise SystemExit(f"No column profiles found for export {export_id!r}. Run 02_profile_export.py first.")
    profiles: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        profiles[str(row["raw_table_name"])].add(str(row["column_name"]))
    return profiles


def detect_drift(contract: dict[str, Any], profiles: dict[str, set[str]]) -> dict[str, Any]:
    tables = table_contracts(contract)
    drift: dict[str, Any] = {
        "missing_tables": sorted(set(tables) - set(profiles)),
        "unexpected_tables": sorted(set(profiles) - set(tables)),
        "tables": {},
    }
    for raw_table_name, table_contract in tables.items():
        expected_columns = set((table_contract.get("columns") or {}).values())
        required_columns = set(table_contract.get("required_columns") or [])
        actual_columns = profiles.get(raw_table_name, set())
        drift["tables"][raw_table_name] = {
            "missing_required_columns": sorted(required_columns - actual_columns),
            "missing_mapped_columns": sorted(expected_columns - actual_columns),
            "unexpected_columns": sorted(actual_columns - expected_columns),
            "matched_columns": sorted(actual_columns & expected_columns),
        }
    return drift


def write_mapping_decisions(engine, export_id: str, contract: dict[str, Any], drift: dict[str, Any]) -> None:
    contract_version = str(contract.get("contract_version") or "unknown")
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM migration_mapping_decision WHERE export_id = :export_id"),
            {"export_id": export_id},
        )
        for raw_table_name, table_contract in table_contracts(contract).items():
            column_map = table_contract.get("columns") or {}
            table_drift = drift["tables"].get(raw_table_name, {})
            actual = set(table_drift.get("matched_columns") or [])
            for canonical_field, raw_column_name in column_map.items():
                matched = raw_column_name in actual
                conn.execute(
                    text(
                        """
                        INSERT INTO migration_mapping_decision(
                            export_id, contract_version, raw_table_name, raw_column_name,
                            canonical_field, decision_type, confidence,
                            requires_human_approval, rationale, evidence
                        )
                        VALUES (
                            :export_id, :contract_version, :raw_table_name, :raw_column_name,
                            :canonical_field, :decision_type, :confidence,
                            :requires_human_approval, :rationale, CAST(:evidence AS jsonb)
                        )
                        """
                    ),
                    {
                        "export_id": export_id,
                        "contract_version": contract_version,
                        "raw_table_name": raw_table_name,
                        "raw_column_name": raw_column_name,
                        "canonical_field": canonical_field,
                        "decision_type": "auto_exact_match" if matched else "missing_column",
                        "confidence": 1.0 if matched else 0.0,
                        "requires_human_approval": not matched,
                        "rationale": "Exact contract column match." if matched else "Contract column missing from profile.",
                        "evidence": json_param({"matched": matched}),
                    },
                )
            for raw_column_name in table_drift.get("unexpected_columns") or []:
                conn.execute(
                    text(
                        """
                        INSERT INTO migration_mapping_decision(
                            export_id, contract_version, raw_table_name, raw_column_name,
                            decision_type, confidence, requires_human_approval,
                            rationale, evidence
                        )
                        VALUES (
                            :export_id, :contract_version, :raw_table_name, :raw_column_name,
                            'unknown_column', 0.0, true, :rationale, CAST(:evidence AS jsonb)
                        )
                        """
                    ),
                    {
                        "export_id": export_id,
                        "contract_version": contract_version,
                        "raw_table_name": raw_table_name,
                        "raw_column_name": raw_column_name,
                        "rationale": "Raw column is not mapped by the current contract.",
                        "evidence": json_param({"preserve_unknown_columns": True}),
                    },
                )


def main() -> None:
    args = parse_args()
    contract = load_contract(args.contract)
    engine = postgres_engine()
    ensure_tables(engine, ["migration_column_profile", "migration_mapping_decision"])
    profiles = fetch_profiles(engine, args.export_id)
    drift = detect_drift(contract, profiles)
    write_mapping_decisions(engine, args.export_id, contract, drift)

    requires_gate = bool(drift["missing_tables"] or drift["unexpected_tables"])
    requires_gate = requires_gate or any(
        table["missing_required_columns"] or table["unexpected_columns"] for table in drift["tables"].values()
    )
    payload = {
        "export_id": args.export_id,
        "contract": args.contract,
        "requires_human_mapping_gate": requires_gate,
        "drift": drift,
    }
    json_path = write_json_report(args.export_id, "schema_drift_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "schema_drift_report.md",
        "Migration V2 Schema Drift Report",
        [
            ("Gate Recommendation", "Human mapping approval is required." if requires_gate else "No drift gate blockers found."),
            ("Missing Tables", "\n".join(f"- `{table}`" for table in drift["missing_tables"]) or "None."),
            ("Unexpected Tables", "\n".join(f"- `{table}`" for table in drift["unexpected_tables"]) or "None."),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
