from __future__ import annotations

import argparse

from _common import (
    config_section,
    ensure_tables,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)
from app.migration_v2.graph.lineage_path_builder import build_lineage_paths


LOGGER = setup_logging("migration_v2.generate_lineage_paths")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the lineage_path read model for migration_v2.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    parser.add_argument("--batch-size", type=int, default=1000, help="PostgreSQL insert batch size.")
    parser.add_argument(
        "--max-paths-per-family",
        type=int,
        default=0,
        help="Optional safety cap per path family. 0 means unlimited.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    v2_config = config_section(load_env_config(args.env_config), "v2") if args.env_config else {}
    engine = postgres_engine_from_url(v2_config["postgres_url"]) if v2_config.get("postgres_url") else postgres_engine()
    ensure_tables(engine, ["catalog_object_staging", "catalog_relationship_staging", "lineage_path"])
    max_paths = args.max_paths_per_family if args.max_paths_per_family > 0 else None
    payload = build_lineage_paths(
        export_id=args.export_id,
        engine=engine,
        batch_size=args.batch_size,
        max_paths_per_family=max_paths,
    )
    json_path = write_json_report(args.export_id, "lineage_path_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "lineage_path_report.md",
        "Migration V2 Lineage Path Report",
        [
            ("Status", f"`{payload['status']}`"),
            ("Objects Read", str(payload["objects_read"])),
            ("Relationships Read", str(payload["relationships_read"])),
            (
                "Path Family Counts",
                "\n".join(
                    f"- `{family}`: {count}"
                    for family, count in sorted(payload["path_family_counts"].items())
                )
                or "None.",
            ),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
