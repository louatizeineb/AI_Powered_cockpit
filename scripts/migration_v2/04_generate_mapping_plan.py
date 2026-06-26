from __future__ import annotations

import argparse

from sqlalchemy import text

from _common import ensure_tables, postgres_engine, setup_logging, write_json_report, write_markdown_report


LOGGER = setup_logging("migration_v2.generate_mapping_plan")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a human-reviewable mapping plan from mapping decisions.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = postgres_engine()
    ensure_tables(engine, ["migration_mapping_decision"])
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT raw_table_name, raw_column_name, canonical_field, decision_type,
                       confidence, requires_human_approval, rationale
                FROM migration_mapping_decision
                WHERE export_id = :export_id
                ORDER BY requires_human_approval DESC, raw_table_name, canonical_field NULLS LAST, raw_column_name
                """
            ),
            {"export_id": args.export_id},
        ).mappings().all()
    decisions = [dict(row) for row in rows]
    requires_approval = [row for row in decisions if row["requires_human_approval"]]
    payload = {
        "export_id": args.export_id,
        "decision_count": len(decisions),
        "requires_human_approval_count": len(requires_approval),
        "decisions": decisions,
    }
    json_path = write_json_report(args.export_id, "mapping_plan.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "mapping_plan.md",
        "Migration V2 Mapping Plan",
        [
            (
                "Summary",
                f"Decisions: `{len(decisions)}`. Human approval required: `{len(requires_approval)}`.",
            ),
            (
                "Approval Items",
                "\n".join(
                    f"- `{row['raw_table_name']}.{row['raw_column_name']}` -> `{row.get('canonical_field')}`: {row['decision_type']}"
                    for row in requires_approval
                )
                or "None.",
            ),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
