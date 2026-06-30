from __future__ import annotations

import argparse

from sqlalchemy import text

from _common import (
    config_section,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)


LOGGER = setup_logging("migration_v2.activate_candidate_search")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the isolated V2 trusted candidate search index.")
    parser.add_argument("--export-id", required=True)
    parser.add_argument("--env-config")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = config_section(load_env_config(args.env_config), "v2") if args.env_config else {}
    engine = postgres_engine_from_url(config["postgres_url"]) if config.get("postgres_url") else postgres_engine()
    with engine.begin() as conn:
        function = conn.execute(text("SELECT to_regprocedure('refresh_migration_v2_candidate_search_documents(text)')")).scalar()
        if not function:
            raise SystemExit("Install migration 019 before activating candidate search.")
        conn.execute(text("DROP INDEX IF EXISTS idx_lineage_search_document_search_text_trgm"))
        conn.execute(text("DROP INDEX IF EXISTS idx_lineage_search_document_search_tsv"))
        state = dict(conn.execute(
            text("SELECT * FROM refresh_migration_v2_candidate_search_documents(:export_id)"),
            {"export_id": args.export_id},
        ).mappings().one())
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_lineage_search_document_search_text_trgm
            ON lineage_search_document USING gin (search_text gin_trgm_ops)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_lineage_search_document_search_tsv
            ON lineage_search_document USING gin (search_tsv)
        """))
    payload = {
        "export_id": args.export_id,
        "status": "ready",
        "scope": "isolated_v2_candidate",
        "graph_version": state["graph_version"],
        "document_count": state["document_count"],
    }
    json_path = write_json_report(args.export_id, "candidate_search_activation_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "candidate_search_activation_report.md",
        "Migration V2 Candidate Search Activation",
        [
            ("Status", "`ready`"),
            ("Scope", "Isolated V2 candidate; production endpoints are unchanged."),
            ("Graph Version", str(state["graph_version"])),
            ("Documents", str(state["document_count"])),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
