from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text

from _common import (
    ROOT,
    config_section,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)


LOGGER = setup_logging("migration_v2.run_baseline")
CATALOG_TABLES = ["source", "container", "structure", "field", "link", "usage"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect or run the old v0 migration baseline for migration_v2.")
    parser.add_argument("--export-id", required=True, help="Export identifier used for report output.")
    parser.add_argument(
        "--run-importer",
        action="store_true",
        help="Run scripts/import_postgres_metadata_lineage_to_neo4j.py before collecting baseline counts.",
    )
    parser.add_argument(
        "--old-importer",
        default=str(ROOT / "scripts" / "import_postgres_metadata_lineage_to_neo4j.py"),
        help="Path to the old baseline importer.",
    )
    parser.add_argument("--env-config", help="Local environment config with a baseline section.")
    return parser.parse_args()


def collect_postgres_counts(postgres_url: str | None = None) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    counts: dict[str, Any] = {}
    try:
        engine = postgres_engine_from_url(postgres_url) if postgres_url else postgres_engine()
        with engine.connect() as conn:
            available = {
                str(row).lower()
                for row in conn.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                        """
                    )
                ).scalars()
            }
            for table_name in CATALOG_TABLES:
                if table_name not in available and f"dg_{table_name}" not in available:
                    counts[table_name] = None
                    continue
                resolved = table_name if table_name in available else f"dg_{table_name}"
                counts[table_name] = int(conn.execute(text(f"SELECT count(*) FROM {resolved}")).scalar_one())
            if "lineage_search_document" in available:
                counts["lineage_search_document"] = int(
                    conn.execute(text("SELECT count(*) FROM lineage_search_document")).scalar_one()
                )
    except BaseException as exc:  # noqa: BLE001 - baseline reports should record failures instead of aborting.
        errors.append(f"postgres_counts_failed: {exc}")
    return counts, errors


def collect_neo4j_counts(
    neo4j_uri: str | None = None,
    neo4j_user: str | None = None,
    neo4j_password: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    counts: dict[str, Any] = {}
    uri = neo4j_uri or os.getenv("NEO4J_URI")
    user = neo4j_user or os.getenv("NEO4J_USER")
    password = neo4j_password or os.getenv("NEO4J_PASSWORD")
    if not uri or not user or not password:
        return counts, ["neo4j_counts_skipped: NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD are required"]
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            queries = {
                "DataGalaxyObject": "MATCH (n:DataGalaxyObject) RETURN count(n) AS count",
                "Source": "MATCH (n:Source) RETURN count(n) AS count",
                "Container": "MATCH (n:Container) RETURN count(n) AS count",
                "Structure": "MATCH (n:Structure) RETURN count(n) AS count",
                "Field": "MATCH (n:Field) RETURN count(n) AS count",
                "BusinessTerm": "MATCH (n:BusinessTerm) RETURN count(n) AS count",
                "Usage": "MATCH (n:Usage) RETURN count(n) AS count",
                "Relationships": "MATCH ()-[r]->() RETURN count(r) AS count",
                "IMPLEMENTS": "MATCH ()-[r:IMPLEMENTS]->() RETURN count(r) AS count",
                "CONTAINS": "MATCH ()-[r:CONTAINS]->() RETURN count(r) AS count",
                "HAS_FIELD": "MATCH ()-[r:HAS_FIELD]->() RETURN count(r) AS count",
            }
            for key, query in queries.items():
                counts[key] = int(session.run(query).single()["count"])
        driver.close()
    except BaseException as exc:  # noqa: BLE001
        errors.append(f"neo4j_counts_failed: {exc}")
    return counts, errors


def run_old_importer(path: Path) -> tuple[str, int | None, str | None]:
    if not path.exists():
        return "failed", None, f"Old importer not found: {path}"
    started = time.perf_counter()
    process = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    if process.returncode != 0:
        return "failed", duration_ms, process.stderr[-4000:] or process.stdout[-4000:]
    return "completed", duration_ms, None


def main() -> None:
    args = parse_args()
    baseline_config: dict[str, Any] = {}
    if args.env_config:
        baseline_config = config_section(load_env_config(args.env_config), "baseline")
    started = time.perf_counter()
    importer_status = "skipped"
    importer_duration_ms = None
    errors: list[str] = []

    if args.run_importer:
        LOGGER.info("Running old baseline importer")
        importer_status, importer_duration_ms, importer_error = run_old_importer(Path(args.old_importer))
        if importer_error:
            errors.append(f"old_importer_failed: {importer_error}")

    postgres_counts, postgres_errors = collect_postgres_counts(baseline_config.get("postgres_url"))
    neo4j_counts, neo4j_errors = collect_neo4j_counts(
        baseline_config.get("neo4j_uri"),
        baseline_config.get("neo4j_user"),
        baseline_config.get("neo4j_password"),
    )
    errors.extend(postgres_errors)
    errors.extend(neo4j_errors)

    duration_ms = int((time.perf_counter() - started) * 1000)
    status = "completed_with_errors" if errors else "completed"
    payload = {
        "export_id": args.export_id,
        "baseline_name": "v0",
        "status": status,
        "old_importer_status": importer_status,
        "old_importer_duration_ms": importer_duration_ms,
        "duration_ms": duration_ms,
        "postgres_counts": postgres_counts,
        "neo4j_counts": neo4j_counts,
        "errors": errors,
    }
    json_path = write_json_report(args.export_id, "baseline_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "baseline_report.md",
        "Migration V2 Baseline Report",
        [
            ("Status", f"`{status}`"),
            ("PostgreSQL Counts", "\n".join(f"- `{key}`: {value}" for key, value in postgres_counts.items()) or "None."),
            ("Neo4j Counts", "\n".join(f"- `{key}`: {value}" for key, value in neo4j_counts.items()) or "None."),
            ("Errors", "\n".join(f"- {error}" for error in errors) or "None."),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)

    # Baseline failures are recorded in the report so orchestration can continue to v2 inspection.
    raise SystemExit(0)


if __name__ == "__main__":
    main()
