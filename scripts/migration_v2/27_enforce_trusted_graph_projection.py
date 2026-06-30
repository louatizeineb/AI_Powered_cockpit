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


LOGGER = setup_logging("migration_v2.enforce_trusted_graph_projection")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove quarantined identities from isolated candidate Neo4j.")
    parser.add_argument("--export-id", required=True)
    parser.add_argument("--env-config")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_env_config(args.env_config) if args.env_config else {}
    v2 = config_section(config, "v2") if config else {}
    engine = postgres_engine_from_url(v2["postgres_url"]) if v2.get("postgres_url") else postgres_engine()
    with engine.connect() as conn:
        quarantine_ids = conn.execute(text("""
            SELECT DISTINCT node_id FROM migration_quarantine_object_projection
            WHERE export_id = :export_id ORDER BY node_id
        """), {"export_id": args.export_id}).scalars().all()
        trusted_count = int(conn.execute(text("""
            SELECT count(DISTINCT node_id) FROM migration_trusted_object_projection
            WHERE export_id = :export_id
        """), {"export_id": args.export_id}).scalar_one())

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(v2["neo4j_uri"], auth=(v2.get("neo4j_user", "neo4j"), v2["neo4j_password"]))
    try:
        with driver.session() as session:
            before = session.run("MATCH (n:DataGalaxyObject) RETURN count(n) AS count").single()["count"]
            present_before = session.run("""
                UNWIND $ids AS id MATCH (n:DataGalaxyObject {node_id: id})
                RETURN count(DISTINCT n) AS count
            """, ids=quarantine_ids).single()["count"]
            deleted = 0
            if not args.dry_run:
                for index in range(0, len(quarantine_ids), args.batch_size):
                    batch = quarantine_ids[index:index + args.batch_size]
                    deleted += int(session.run("""
                        UNWIND $ids AS id
                        MATCH (n:DataGalaxyObject {node_id: id})
                        DETACH DELETE n
                        RETURN count(*) AS count
                    """, ids=batch).single()["count"])
            after = session.run("MATCH (n:DataGalaxyObject) RETURN count(n) AS count").single()["count"]
            remaining = session.run("""
                UNWIND $ids AS id MATCH (n:DataGalaxyObject {node_id: id})
                RETURN count(DISTINCT n) AS count
            """, ids=quarantine_ids).single()["count"]
            relationship_count = session.run("""
                MATCH (:DataGalaxyObject)-[relationship]->(:DataGalaxyObject)
                RETURN count(relationship) AS count
            """).single()["count"]
    finally:
        driver.close()

    status = "ready" if remaining == 0 and after == trusted_count else "blocked"
    payload = {
        "export_id": args.export_id,
        "status": status,
        "dry_run": args.dry_run,
        "trusted_distinct_node_count": trusted_count,
        "candidate_nodes_before": before,
        "candidate_nodes_after": after,
        "quarantine_nodes_present_before": present_before,
        "quarantine_nodes_deleted": deleted,
        "quarantine_nodes_remaining": remaining,
        "candidate_relationship_count": relationship_count,
    }
    json_path = write_json_report(args.export_id, "trusted_graph_projection_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "trusted_graph_projection_report.md",
        "Migration V2 Trusted Graph Projection",
        [
            ("Status", f"`{status}`"),
            ("Nodes", f"- before: {before}\n- after: {after}\n- trusted expected: {trusted_count}"),
            ("Quarantine", f"- deleted: {deleted}\n- remaining: {remaining}"),
            ("Relationships", str(relationship_count)),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)
    if status != "ready":
        raise SystemExit("Candidate graph does not match the trusted projection.")


if __name__ == "__main__":
    main()
