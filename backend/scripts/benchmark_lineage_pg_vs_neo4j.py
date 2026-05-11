from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase
from sqlalchemy import create_engine, text


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

POSTGRES_URL = os.getenv("POSTGRES_URL")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

RESULTS_DIR = ROOT_DIR / "benchmark_results"
RESULTS_DIR.mkdir(exist_ok=True)


def require_env() -> None:
    missing = []
    if not POSTGRES_URL:
        missing.append("POSTGRES_URL")
    if not NEO4J_PASSWORD:
        missing.append("NEO4J_PASSWORD")

    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * p))
    return ordered[index]


def summarize(times_ms: list[float], payloads: list[dict[str, Any]]) -> dict[str, Any]:
    last_payload = payloads[-1] if payloads else {}

    return {
        "runs": len(times_ms),
        "min_ms": round(min(times_ms), 3),
        "max_ms": round(max(times_ms), 3),
        "mean_ms": round(statistics.mean(times_ms), 3),
        "median_ms": round(statistics.median(times_ms), 3),
        "p95_ms": round(percentile(times_ms, 0.95), 3),
        "last_node_count": last_payload.get("node_count"),
        "last_edge_count": last_payload.get("edge_count"),
        "last_max_depth": last_payload.get("max_depth"),
    }


def resolve_link_table(pg_engine) -> str:
    query = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
      AND table_name IN ('link', 'dg_link')
    ORDER BY CASE WHEN table_name = 'link' THEN 1 ELSE 2 END
    """

    with pg_engine.connect() as conn:
        rows = conn.execute(text(query)).mappings().all()

    if not rows:
        raise RuntimeError("No lineage table found. Expected table 'link' or 'dg_link'.")

    return rows[0]["table_name"]


def postgres_lineage_query(
    pg_engine,
    node_id: str,
    depth: int,
    max_edges: int,
) -> dict[str, Any]:
    table = resolve_link_table(pg_engine)

    query = text(f"""
    WITH RECURSIVE walk AS (
        SELECT
            1 AS depth,
            l.src_node_id,
            l.tgt_node_id,
            l.link_type,
            CASE
                WHEN l.src_node_id = :node_id THEN l.tgt_node_id
                ELSE l.src_node_id
            END AS current_node,
            ARRAY[
                CAST(:node_id AS TEXT),
                CAST(
                    CASE
                        WHEN l.src_node_id = :node_id THEN l.tgt_node_id
                        ELSE l.src_node_id
                    END
                AS TEXT)
            ] AS path
        FROM {table} l
        WHERE l.link_type IN ('IsInputOf', 'IsOutputOf', 'IS_INPUT_OF', 'IS_OUTPUT_OF')
          AND (
                l.src_node_id = :node_id
             OR l.tgt_node_id = :node_id
          )

        UNION ALL

        SELECT
            w.depth + 1 AS depth,
            l.src_node_id,
            l.tgt_node_id,
            l.link_type,
            CASE
                WHEN l.src_node_id = w.current_node THEN l.tgt_node_id
                ELSE l.src_node_id
            END AS current_node,
            w.path || CAST(
                CASE
                    WHEN l.src_node_id = w.current_node THEN l.tgt_node_id
                    ELSE l.src_node_id
                END
            AS TEXT) AS path
        FROM walk w
        JOIN {table} l
          ON (
                l.src_node_id = w.current_node
             OR l.tgt_node_id = w.current_node
          )
        WHERE w.depth < :depth
          AND l.link_type IN ('IsInputOf', 'IsOutputOf', 'IS_INPUT_OF', 'IS_OUTPUT_OF')
          AND NOT (
            CASE
                WHEN l.src_node_id = w.current_node THEN l.tgt_node_id
                ELSE l.src_node_id
            END = ANY(w.path)
          )
    ),
    limited AS (
        SELECT *
        FROM walk
        LIMIT :max_edges
    )
    SELECT
        COUNT(*) AS edge_count,
        COUNT(DISTINCT current_node) + 1 AS node_count,
        COALESCE(MAX(depth), 0) AS max_depth
    FROM limited
    """)

    with pg_engine.connect() as conn:
        row = conn.execute(
            query,
            {
                "node_id": node_id,
                "depth": depth,
                "max_edges": max_edges,
            },
        ).mappings().one()

    return {
        "node_count": int(row["node_count"] or 0),
        "edge_count": int(row["edge_count"] or 0),
        "max_depth": int(row["max_depth"] or 0),
    }


def neo4j_lineage_query(
    neo4j_driver,
    node_id: str,
    depth: int,
    max_paths: int,
) -> dict[str, Any]:
    query = f"""
    MATCH (root {{node_id: $node_id}})
    MATCH p = (root)-[:IS_INPUT_OF|IS_OUTPUT_OF*1..{depth}]-(neighbor)
    WITH p
    LIMIT $max_paths

    WITH collect(p) AS paths

    CALL {{
        WITH paths
        UNWIND paths AS p
        UNWIND nodes(p) AS n
        RETURN collect(DISTINCT coalesce(n.node_id, n.usage_uuid, elementId(n))) AS node_ids
    }}

    CALL {{
        WITH paths
        UNWIND paths AS p
        UNWIND relationships(p) AS r
        RETURN collect(DISTINCT elementId(r)) AS rel_ids
    }}

    RETURN
        size(node_ids) AS node_count,
        size(rel_ids) AS edge_count
    """

    with neo4j_driver.session() as session:
        record = session.run(
            query,
            node_id=node_id,
            max_paths=max_paths,
        ).single()

    if record is None:
        return {
            "node_count": 0,
            "edge_count": 0,
            "max_depth": 0,
        }

    return {
        "node_count": int(record["node_count"] or 0),
        "edge_count": int(record["edge_count"] or 0),
        "max_depth": depth,
    }


def benchmark_method(
    name: str,
    fn,
    runs: int,
    warmups: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payloads: list[dict[str, Any]] = []
    times_ms: list[float] = []

    for _ in range(warmups):
        fn()

    for _ in range(runs):
        started = time.perf_counter()
        payload = fn()
        elapsed_ms = (time.perf_counter() - started) * 1000

        times_ms.append(elapsed_ms)
        payloads.append(payload)

    summary = summarize(times_ms, payloads)
    summary["method"] = name

    return summary, payloads


def write_reports(
    node_id: str,
    depth: int,
    summaries: list[dict[str, Any]],
) -> None:
    safe_node = node_id.replace(":", "_").replace("/", "_").replace("\\", "_")
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    csv_path = RESULTS_DIR / f"lineage_benchmark_{safe_node}_{timestamp}.csv"
    json_path = RESULTS_DIR / f"lineage_benchmark_{safe_node}_{timestamp}.json"

    fieldnames = [
        "method",
        "runs",
        "min_ms",
        "max_ms",
        "mean_ms",
        "median_ms",
        "p95_ms",
        "last_node_count",
        "last_edge_count",
        "last_max_depth",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summaries)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "node_id": node_id,
                "depth": depth,
                "results": summaries,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"\nCSV report : {csv_path}")
    print(f"JSON report: {json_path}")


def print_summary(summaries: list[dict[str, Any]]) -> None:
    print("\nLINEAGE BENCHMARK RESULTS")
    print("=" * 100)
    print(
        f"{'method':<14} "
        f"{'mean_ms':>10} "
        f"{'median_ms':>10} "
        f"{'p95_ms':>10} "
        f"{'nodes':>10} "
        f"{'edges':>10} "
        f"{'depth':>8}"
    )
    print("-" * 100)

    for row in summaries:
        print(
            f"{row['method']:<14} "
            f"{row['mean_ms']:>10} "
            f"{row['median_ms']:>10} "
            f"{row['p95_ms']:>10} "
            f"{str(row['last_node_count']):>10} "
            f"{str(row['last_edge_count']):>10} "
            f"{str(row['last_max_depth']):>8}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark one lineage query using PostgreSQL vs Neo4j."
    )
    parser.add_argument("--node-id", required=True, help="Starting DataGalaxy node_id.")
    parser.add_argument("--depth", type=int, default=2, help="Traversal depth.")
    parser.add_argument("--runs", type=int, default=5, help="Measured runs.")
    parser.add_argument("--warmups", type=int, default=1, help="Warmup runs.")
    parser.add_argument("--max-edges", type=int, default=10000, help="Postgres max edges.")
    parser.add_argument("--max-paths", type=int, default=10000, help="Neo4j max paths.")
    args = parser.parse_args()

    require_env()

    pg_engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    neo4j_driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        connection_timeout=60,
        max_connection_lifetime=3600,
    )

    print("Benchmarking lineage fetch")
    print(f"node_id   : {args.node_id}")
    print(f"depth     : {args.depth}")
    print(f"runs      : {args.runs}")
    print(f"warmups   : {args.warmups}")
    print(f"max_edges : {args.max_edges}")
    print(f"max_paths : {args.max_paths}")

    try:
        pg_summary, _ = benchmark_method(
            "postgres",
            lambda: postgres_lineage_query(
                pg_engine=pg_engine,
                node_id=args.node_id,
                depth=args.depth,
                max_edges=args.max_edges,
            ),
            runs=args.runs,
            warmups=args.warmups,
        )

        neo_summary, _ = benchmark_method(
            "neo4j",
            lambda: neo4j_lineage_query(
                neo4j_driver=neo4j_driver,
                node_id=args.node_id,
                depth=args.depth,
                max_paths=args.max_paths,
            ),
            runs=args.runs,
            warmups=args.warmups,
        )

        summaries = [pg_summary, neo_summary]
        print_summary(summaries)
        write_reports(args.node_id, args.depth, summaries)

    finally:
        neo4j_driver.close()


if __name__ == "__main__":
    main()