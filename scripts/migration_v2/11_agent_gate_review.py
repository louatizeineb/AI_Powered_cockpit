from __future__ import annotations

import argparse

from _common import setup_logging, write_json_report, write_markdown_report
from app.migration_v2.orchestration.migration_orchestrator import recommend_gates


LOGGER = setup_logging("migration_v2.agent_gate_review")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read migration_v2 reports and produce an agent-style human gate recommendation."
    )
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = recommend_gates(args.export_id)
    json_path = write_json_report(args.export_id, "agent_gate_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "agent_gate_report.md",
        "Migration V2 Agent Gate Report",
        [
            ("Recommendation", f"`{payload['status']}`"),
            ("Principle", payload["principle"]),
            (
                "Gates",
                "\n".join(
                    f"- `{item['gate']}`: `{item['status']}` - {item['reason']} Evidence: `{item['evidence']}`"
                    for item in payload["gates"]
                )
                or "No gates evaluated.",
            ),
            (
                "Missing Reports",
                "\n".join(f"- `{name}`" for name in payload["missing_reports"]) or "None.",
            ),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
