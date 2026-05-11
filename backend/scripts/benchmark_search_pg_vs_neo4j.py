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


SEARCH_TABLES = [
    ("source", "Source", "node_id", ["name_label", "name_tech", "path_full", "app_code"]),
    ("container", "Container", "node_id", ["name_label", "name_tech", "path_full"]),
    ("structure", "Structure", "node_id", ["name_label", "name_tech", "path_full"]),
    ("field", "Field", "node_id", ["name_label", "name_tech", "path_full"]),
    ("usage", "Usage", "usage_uuid", ["usage_name", "usage_tech_name", "usage_path", "dataset_ref", "app_code"]),
]


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
        "last_result_count": last_payload.get("count"),
    }


def existing_postgres_tables(pg_engine) -> set[str]:
    query = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
    """

    with pg_engine.connect() as conn:
        rows = conn.execute(text(query)).mappings().all()

    return {row["table_name"] for row in rows}


def table_columns(pg_engine, table_name: str) -> set[str]:
    query = """
    SELECT column_name
    FROM information_schema.columns
    WHERE table_name = :table_name
    """

    with pg_engine.connect() as conn:
        rows = conn.execute(text(query), {"table_name": table_name}).mappings().all()

    return {row["column_name"] for row in rows}


def postgres_search(
    pg_engine,
    q: str,
    limit: int,
) -> dict[str, Any]:
    existing = existing_postgres_tables(pg_engine)
    pattern = f"%{q}%"

    results: list[dict[str, Any]] = []

    for table_name, asset_type, pk, candidate_columns in SEARCH_TABLES:
        if table_name not in existing:
            continue

        cols = table_columns(pg_engine, table_name)
        searchable_cols = [c for c in candidate_columns if c in cols]

        if not searchable_cols:
            continue

        name_col = (
            "name_label"
            if "name_label" in cols
            else "usage_name"
            if "usage_name" in cols
            else pk
        )

        tech_col = (
            "name_tech"
            if "name_tech" in cols
            else "usage_tech_name"
            if "usage_tech_name" in cols
            else pk
        )

        path_col = (
            "path_full"
            if "path_full" in cols
            else "usage_path"
            if "usage_path" in cols
            else pk
        )

        where_clause = " OR ".join(
            f"LOWER(CAST({col} AS TEXT)) LIKE LOWER(:pattern)"
            for col in searchable_cols
        )

        query = text(f"""
        SELECT
            CAST({pk} AS TEXT) AS node_id,
            CAST({name_col} AS TEXT) AS name,
            CAST({tech_col} AS TEXT) AS technical_name,
            CAST({path_col} AS TEXT) AS path
        FROM {table_name}
        WHERE {where_clause}
        LIMIT :limit
        """)

        remaining = limit - len(results)

        if remaining <= 0:
            break

        with pg_engine.connect() as conn:
            rows = conn.execute(
                query,
                {
                    "pattern": pattern,
                    "limit": remaining,
                },
            ).mappings().all()

        for row in rows:
            results.append(
                {
                    "id": f"{asset_type.lower()}:{row['node_id']}",
                    "node_id": row["node_id"],
                    "type": asset_type,
                    "name": row["name"],
                    "technical_name": row["technical_name"],
                    "path": row["path"],
                }
            )

    return {
        "count": len(results),
        "results": results,
    }


def neo4j_search(
    neo4j_driver,
    q: str,
    limit: int,
) -> dict[str, Any]:
    query = """
    MATCH (n)
    WHERE any(
        key IN [
            'name_label',
            'name_tech',
            'path_full',
            'name',
            'usage_name',
            'usage_tech_name',
            'usage_path',
            'dataset_ref',
            'app_code'
        ]
        WHERE toLower(toString(coalesce(n[key], ''))) CONTAINS toLower($q)
    )
    RETURN
        coalesce(n.node_id, n.usage_uuid, elementId(n)) AS node_id,
        labels(n) AS labels,
        coalesce(n.name_label, n.name, n.usage_name, n.name_tech, n.usage_tech_name) AS name,
        coalesce(n.name_tech, n.usage_tech_name) AS technical_name,
        coalesce(n.path_full, n.usage_path) AS path
    LIMIT $limit
    """

    with neo4j_driver.session() as session:
        rows = list(session.run(query, q=q, limit=limit))

    results = []

    for row in rows:
        labels = row["labels"] or []
        asset_type = labels[0] if labels else "Node"
        node_id = row["node_id"]

        results.append(
            {
                "id": f"{asset_type.lower()}:{node_id}",
                "node_id": node_id,
                "type": asset_type,
                "name": row["name"],
                "technical_name": row["technical_name"],
                "path": row["path"],
            }
        )

    return {
        "count": len(results),
        "results": results,
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
    query_text: str,
    limit: int,
    summaries: list[dict[str, Any]],
) -> None:
    safe_q = query_text.replace(" ", "_").replace("/", "_").replace("\\", "_")[:50]
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    csv_path = RESULTS_DIR / f"search_benchmark_{safe_q}_{timestamp}.csv"
    json_path = RESULTS_DIR / f"search_benchmark_{safe_q}_{timestamp}.json"

    fieldnames = [
        "method",
        "runs",
        "min_ms",
        "max_ms",
        "mean_ms",
        "median_ms",
        "p95_ms",
        "last_result_count",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summaries)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "query": query_text,
                "limit": limit,
                "results": summaries,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"\nCSV report : {csv_path}")
    print(f"JSON report: {json_path}")


def print_summary(summaries: list[dict[str, Any]]) -> None:
    print("\nSEARCH BENCHMARK RESULTS")
    print("=" * 80)
    print(
        f"{'method':<14} "
        f"{'mean_ms':>10} "
        f"{'median_ms':>10} "
        f"{'p95_ms':>10} "
        f"{'results':>10}"
    )
    print("-" * 80)

    for row in summaries:
        print(
            f"{row['method']:<14} "
            f"{row['mean_ms']:>10} "
            f"{row['median_ms']:>10} "
            f"{row['p95_ms']:>10} "
            f"{str(row['last_result_count']):>10}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark search using PostgreSQL vs Neo4j."
    )
    parser.add_argument("--q", required=True, help="Search query.")
    parser.add_argument("--limit", type=int, default=20, help="Max results.")
    parser.add_argument("--runs", type=int, default=5, help="Measured runs.")
    parser.add_argument("--warmups", type=int, default=1, help="Warmup runs.")
    args = parser.parse_args()

    require_env()

    pg_engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    neo4j_driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        connection_timeout=60,
        max_connection_lifetime=3600,
    )

    print("Benchmarking search")
    print(f"query   : {args.q}")
    print(f"limit   : {args.limit}")
    print(f"runs    : {args.runs}")
    print(f"warmups : {args.warmups}")

    try:
        pg_summary, _ = benchmark_method(
            "postgres",
            lambda: postgres_search(
                pg_engine=pg_engine,
                q=args.q,
                limit=args.limit,
            ),
            runs=args.runs,
            warmups=args.warmups,
        )

        neo_summary, _ = benchmark_method(
            "neo4j",
            lambda: neo4j_search(
                neo4j_driver=neo4j_driver,
                q=args.q,
                limit=args.limit,
            ),
            runs=args.runs,
            warmups=args.warmups,
        )

        summaries = [pg_summary, neo_summary]
        print_summary(summaries)
        write_reports(args.q, args.limit, summaries)

    finally:
        neo4j_driver.close()


if __name__ == "__main__":
    main()