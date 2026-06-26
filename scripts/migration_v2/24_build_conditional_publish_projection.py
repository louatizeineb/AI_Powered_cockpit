from __future__ import annotations

import argparse
import json

from _common import (
    config_section,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)
from app.migration_v2.governance.publication_policy import apply_conditional_policy


LOGGER = setup_logging("migration_v2.conditional_publish")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify staging into trusted and governance projections.")
    parser.add_argument("--export-id", required=True)
    parser.add_argument("--env-config")
    parser.add_argument("--policy-version", default="conditional-publish-v1")
    parser.add_argument("--decided-by", default="deterministic_policy_engine")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = config_section(load_env_config(args.env_config), "v2") if args.env_config else {}
    engine = postgres_engine_from_url(config["postgres_url"]) if config.get("postgres_url") else postgres_engine()
    result = apply_conditional_policy(
        engine, args.export_id, policy_version=args.policy_version, decided_by=args.decided_by
    )
    json_path = write_json_report(args.export_id, "conditional_publish_report.json", result)
    md_path = write_markdown_report(
        args.export_id,
        "conditional_publish_report.md",
        "Migration V2 Conditional Publish Report",
        [
            ("Status", f"`{result['status']}`"),
            ("Objects", json.dumps(result["object_counts"], indent=2)),
            ("Relationships", json.dumps(result["relationship_counts"], indent=2)),
            ("Review Pending", json.dumps(result["aggregate_reviews"], indent=2) if result["aggregate_reviews"] else "None."),
            ("Hard Blockers", json.dumps(result["hard_blockers"], indent=2) if result["hard_blockers"] else "None."),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
