from __future__ import annotations

import argparse
import json
import os
from typing import Any

from sqlalchemy import text

from _common import (
    REPORT_ROOT,
    config_section,
    ensure_tables,
    json_param,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)
from app.migration_v2.graph.graph_auditor import audit_graph


LOGGER = setup_logging("migration_v2.audit_and_compare")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit migration_v2 staging and compare it with the old v0 baseline.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    return parser.parse_args()


def load_baseline(export_id: str) -> dict[str, Any] | None:
    path = REPORT_ROOT / export_id / "baseline_report.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def collect_v2_counts(engine, export_id: str) -> dict[str, Any]:
    with engine.connect() as conn:
        object_counts = conn.execute(
            text(
                """
                SELECT object_type, count(*) AS count
                FROM catalog_object_staging
                WHERE export_id = :export_id
                GROUP BY object_type
                ORDER BY object_type
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
        relationship_counts = conn.execute(
            text(
                """
                SELECT relationship_type, count(*) AS count
                FROM catalog_relationship_staging
                WHERE export_id = :export_id
                GROUP BY relationship_type
                ORDER BY relationship_type
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
        finding_counts = conn.execute(
            text(
                """
                SELECT severity, category, count(*) AS count
                FROM migration_validation_finding
                WHERE export_id = :export_id
                GROUP BY severity, category
                ORDER BY severity, category
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
        relationship_source_counts = conn.execute(
            text(
                """
                SELECT source_table, count(*) AS count
                FROM catalog_relationship_staging
                WHERE export_id = :export_id
                GROUP BY source_table
                ORDER BY source_table
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
    return {
        "objects": {row["object_type"]: int(row["count"]) for row in object_counts},
        "relationships": {row["relationship_type"]: int(row["count"]) for row in relationship_counts},
        "relationships_by_source_table": {
            row["source_table"] or "<null>": int(row["count"])
            for row in relationship_source_counts
        },
        "findings": [dict(row) for row in finding_counts],
    }


def collect_neo4j_candidate_counts(v2_config: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    v2_config = v2_config or {}
    uri = v2_config.get("neo4j_uri") or os.getenv("NEO4J_URI")
    user = v2_config.get("neo4j_user") or os.getenv("NEO4J_USER")
    password = v2_config.get("neo4j_password") or os.getenv("NEO4J_PASSWORD")
    if not uri or not user or not password:
        return {}, ["neo4j_audit_skipped: NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD are required"]
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            rows = session.run(
                """
                MATCH (n)
                UNWIND labels(n) AS label
                RETURN label, count(*) AS count
                ORDER BY label
                """
            )
            node_counts = {row["label"]: int(row["count"]) for row in rows}
            rel_rows = session.run(
                """
                MATCH ()-[r]->()
                RETURN type(r) AS relationship_type, count(*) AS count
                ORDER BY relationship_type
                """
            )
            relationship_counts = {row["relationship_type"]: int(row["count"]) for row in rel_rows}
        driver.close()
        return {"nodes": node_counts, "relationships": relationship_counts}, []
    except BaseException as exc:  # noqa: BLE001
        return {}, [f"neo4j_audit_failed: {exc}"]


def neo4j_driver_from_config(v2_config: dict[str, Any] | None = None):
    v2_config = v2_config or {}
    uri = v2_config.get("neo4j_uri") or os.getenv("NEO4J_URI")
    user = v2_config.get("neo4j_user") or os.getenv("NEO4J_USER")
    password = v2_config.get("neo4j_password") or os.getenv("NEO4J_PASSWORD")
    if not uri or not user or not password:
        return None, ["neo4j_graph_audit_skipped: NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD are required"]
    try:
        from neo4j import GraphDatabase

        return GraphDatabase.driver(uri, auth=(user, password)), []
    except BaseException as exc:  # noqa: BLE001
        return None, [f"neo4j_graph_audit_driver_failed: {exc}"]


def benchmark_rows(export_id: str, baseline: dict[str, Any] | None, v2_counts: dict[str, Any]) -> list[dict[str, Any]]:
    if not baseline:
        return []
    rows: list[dict[str, Any]] = []
    baseline_pg = baseline.get("postgres_counts") or {}
    table_to_object = {
        "source": "Source",
        "container": "Container",
        "structure": "Structure",
        "field": "Field",
    }
    for table_name, object_type in table_to_object.items():
        baseline_value = baseline_pg.get(table_name)
        v2_value = v2_counts["objects"].get(object_type)
        if baseline_value is None or v2_value is None:
            continue
        delta = v2_value - baseline_value
        delta_pct = None if baseline_value == 0 else round(delta / baseline_value * 100, 4)
        rows.append(
            {
                "export_id": export_id,
                "metric_name": f"{object_type}_count",
                "baseline_value": baseline_value,
                "v2_value": v2_value,
                "delta_value": delta,
                "delta_pct": delta_pct,
                "status": "matched" if delta == 0 else "different",
            }
        )
    baseline_link = baseline_pg.get("link")
    resolver_rels = int(v2_counts.get("relationships_by_source_table", {}).get("usage_resolver", 0))
    v2_rels = sum(v2_counts["relationships"].values()) - resolver_rels
    if baseline_link is not None:
        delta = v2_rels - baseline_link
        rows.append(
            {
                "export_id": export_id,
                "metric_name": "relationship_staging_vs_link_rows",
                "baseline_value": baseline_link,
                "v2_value": v2_rels,
                "delta_value": delta,
                "delta_pct": None if baseline_link == 0 else round(delta / baseline_link * 100, 4),
                "status": "matched" if delta == 0 else "different",
            }
        )
    if resolver_rels:
        rows.append(
            {
                "export_id": export_id,
                "metric_name": "usage_resolver_relationship_rows",
                "baseline_value": 0,
                "v2_value": resolver_rels,
                "delta_value": resolver_rels,
                "delta_pct": None,
                "status": "v2_enrichment",
            }
        )
    return rows


def relationship_parity_rows(
    export_id: str,
    baseline: dict[str, Any] | None,
    graph_audit: dict[str, Any],
) -> list[dict[str, Any]]:
    if not baseline:
        return []
    baseline_neo4j = baseline.get("neo4j_counts") or {}
    v2_graph = graph_audit.get("neo4j_graph") or {}
    v2_relationship_counts = v2_graph.get("relationship_counts_by_type") or {}
    metrics: list[tuple[str, Any, Any]] = [
        ("Relationships", baseline_neo4j.get("Relationships"), v2_graph.get("total_relationships")),
    ]
    for rel_type in sorted(set(v2_relationship_counts) | set(baseline_neo4j)):
        if rel_type in {"DataGalaxyObject", "Source", "Container", "Structure", "Field", "BusinessTerm", "Usage", "Relationships"}:
            continue
        baseline_value = baseline_neo4j.get(rel_type)
        v2_value = v2_relationship_counts.get(rel_type)
        if baseline_value is None and v2_value is None:
            continue
        metrics.append((rel_type, baseline_value, v2_value))

    rows: list[dict[str, Any]] = []
    for metric_name, baseline_value, v2_value in metrics:
        if baseline_value is None:
            status = "v2_extra"
            delta = v2_value
            delta_pct = None
        elif v2_value is None:
            status = "missing_in_v2"
            delta = -baseline_value
            delta_pct = -100.0 if baseline_value else None
        else:
            delta = v2_value - baseline_value
            delta_pct = None if baseline_value == 0 else round(delta / baseline_value * 100, 4)
            status = "matched" if delta == 0 else "different"
        rows.append(
            {
                "export_id": export_id,
                "metric_name": metric_name,
                "baseline_value": baseline_value,
                "v2_value": v2_value,
                "delta_value": delta,
                "delta_pct": delta_pct,
                "status": status,
            }
        )
    return rows


def write_benchmark_rows(engine, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    export_id = rows[0]["export_id"]
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM migration_benchmark_result WHERE export_id = :export_id"),
            {"export_id": export_id},
        )
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO migration_benchmark_result(
                        export_id, baseline_name, metric_name, baseline_value, v2_value,
                        delta_value, delta_pct, status, evidence
                    )
                    VALUES (
                        :export_id, 'v0', :metric_name, :baseline_value, :v2_value,
                        :delta_value, :delta_pct, :status, CAST(:evidence AS jsonb)
                    )
                    """
                ),
                {**row, "evidence": json_param({"source": "09_audit_and_compare.py"})},
            )


def main() -> None:
    args = parse_args()
    v2_config: dict[str, Any] = {}
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
    engine = postgres_engine_from_url(v2_config["postgres_url"]) if v2_config.get("postgres_url") else postgres_engine()
    ensure_tables(
        engine,
        [
            "catalog_object_staging",
            "catalog_relationship_staging",
            "migration_validation_finding",
            "migration_benchmark_result",
        ],
    )
    baseline = load_baseline(args.export_id)
    v2_counts = collect_v2_counts(engine, args.export_id)
    neo4j_counts, neo4j_errors = collect_neo4j_candidate_counts(v2_config)
    driver, graph_driver_errors = neo4j_driver_from_config(v2_config)
    try:
        graph_audit = audit_graph(args.export_id, engine, driver=driver)
    finally:
        if driver is not None:
            driver.close()
    if graph_driver_errors:
        graph_audit["errors"] = [*graph_audit.get("errors", []), *graph_driver_errors]
        if graph_audit.get("status") == "completed":
            graph_audit["status"] = "completed_with_warnings"
    graph_audit_json_path = write_json_report(args.export_id, "graph_audit_report.json", graph_audit)
    graph_audit_md_path = write_markdown_report(
        args.export_id,
        "graph_audit_report.md",
        "Migration V2 Graph Audit Report",
        [
            ("Status", f"`{graph_audit['status']}`"),
            (
                "Hierarchy Classifications",
                "\n".join(
                    f"- `{key}`: {value}"
                    for key, value in graph_audit["staging_hierarchy"]["hierarchy_classification_counts"].items()
                )
                or "None.",
            ),
            (
                "Hierarchy Health",
                "\n".join(
                    [
                        f"- `missing_parent_count`: {graph_audit['staging_hierarchy']['missing_parent_count']}",
                        f"- `cycle_count`: {graph_audit['staging_hierarchy']['cycle_count']}",
                        f"- `duplicate_role_node_count`: {graph_audit['staging_hierarchy']['duplicate_role_node_count']}",
                        f"- `irregular_allowed_count`: {graph_audit['staging_hierarchy']['irregular_allowed_count']}",
                    ]
                ),
            ),
            (
                "Neo4j Graph",
                "\n".join(
                    f"- `{key}`: {value}"
                    for key, value in graph_audit.get("neo4j_graph", {}).items()
                    if key not in {
                        "relationship_counts_by_type",
                        "hierarchy_depth_distribution",
                        "orphan_classification_counts",
                        "orphan_counts_by_object_type",
                        "orphan_samples",
                    }
                )
                or "No Neo4j graph audit available.",
            ),
            (
                "Orphan Classifications",
                "\n".join(
                    f"- `{key}`: {value}"
                    for key, value in (graph_audit.get("neo4j_graph", {}).get("orphan_classification_counts") or {}).items()
                )
                or "No orphan classification available.",
            ),
        ],
    )
    rows = benchmark_rows(args.export_id, baseline, v2_counts)
    parity_rows = relationship_parity_rows(args.export_id, baseline, graph_audit)
    parity_json_path = write_json_report(
        args.export_id,
        "relationship_parity_report.json",
        {
            "export_id": args.export_id,
            "baseline_report_found": baseline is not None,
            "rows": parity_rows,
        },
    )
    parity_md_path = write_markdown_report(
        args.export_id,
        "relationship_parity_report.md",
        "Migration V2 Relationship Parity Report",
        [
            ("Baseline", "Found baseline report." if baseline else "No baseline report found."),
            (
                "Rows",
                "\n".join(
                    f"- `{row['metric_name']}`: v0={row['baseline_value']} "
                    f"v2={row['v2_value']} delta={row['delta_value']} status=`{row['status']}`"
                    for row in parity_rows
                )
                or "No relationship parity rows.",
            ),
        ],
    )
    write_benchmark_rows(engine, rows)

    payload = {
        "export_id": args.export_id,
        "baseline_report_found": baseline is not None,
        "v2_counts": v2_counts,
        "neo4j_counts": neo4j_counts,
        "neo4j_errors": neo4j_errors,
        "graph_audit_report": str(graph_audit_json_path),
        "graph_audit": graph_audit,
        "relationship_parity_report": str(parity_json_path),
        "relationship_parity_rows": parity_rows,
        "benchmark_rows": rows,
    }
    json_path = write_json_report(args.export_id, "audit_compare_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "audit_compare_report.md",
        "Migration V2 Audit And Compare Report",
        [
            ("Baseline", "Found baseline report." if baseline else "No baseline report found. Run 00_run_baseline.py."),
            ("V2 Object Counts", "\n".join(f"- `{key}`: {value}" for key, value in v2_counts["objects"].items()) or "None."),
            (
                "Graph Audit",
                "\n".join(
                    [
                        f"- `missing_parent_count`: {graph_audit['staging_hierarchy']['missing_parent_count']}",
                        f"- `cycle_count`: {graph_audit['staging_hierarchy']['cycle_count']}",
                        f"- `irregular_allowed_count`: {graph_audit['staging_hierarchy']['irregular_allowed_count']}",
                        f"- `duplicate_role_node_count`: {graph_audit['staging_hierarchy']['duplicate_role_node_count']}",
                        f"- `actionable_orphan_count`: {graph_audit.get('neo4j_graph', {}).get('actionable_orphan_count')}",
                    ]
                ),
            ),
            (
                "Relationship Parity",
                "\n".join(
                    f"- `{row['metric_name']}`: v0={row['baseline_value']} v2={row['v2_value']} delta={row['delta_value']}"
                    for row in parity_rows
                )
                or "No relationship parity rows.",
            ),
            (
                "Benchmark",
                "\n".join(
                    f"- `{row['metric_name']}`: v0={row['baseline_value']} v2={row['v2_value']} delta={row['delta_value']}"
                    for row in rows
                )
                or "No comparable benchmark rows.",
            ),
        ],
    )
    LOGGER.info(
        "Wrote %s, %s, %s, %s, %s and %s",
        graph_audit_json_path,
        graph_audit_md_path,
        parity_json_path,
        parity_md_path,
        json_path,
        md_path,
    )


if __name__ == "__main__":
    main()
