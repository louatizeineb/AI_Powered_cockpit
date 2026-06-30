from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

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


LOGGER = setup_logging("migration_v2.fast_search_benchmark")


DEFAULT_QUERIES = [
    ("label", "ALM"),
    ("label", "AGP"),
    ("label", "OAD"),
    ("typo", "TSGCODe"),
    ("accented", "échéance"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the fast hybrid lineage search endpoint.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8001", help="Backend API base URL.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    parser.add_argument("--limit", type=int, default=20, help="Search result limit.")
    parser.add_argument("--runs", type=int, default=5, help="Runs per query after the cold request.")
    parser.add_argument("--exact-node-id", help="Optional exact node id benchmark query.")
    parser.add_argument("--full-path", help="Optional full path benchmark query.")
    parser.add_argument("--technical-name", help="Optional technical name benchmark query.")
    parser.add_argument("--search-path", default="/lineage/explorer/search", help="Search endpoint path.")
    return parser.parse_args()


def engine_from_args(args: argparse.Namespace):
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
        if v2_config.get("postgres_url"):
            return postgres_engine_from_url(v2_config["postgres_url"])
    return postgres_engine()


def discover_queries(args: argparse.Namespace) -> list[tuple[str, str]]:
    queries = list(DEFAULT_QUERIES)
    exact = args.exact_node_id
    full_path = args.full_path
    technical_name = args.technical_name
    try:
        engine = engine_from_args(args)
        with engine.connect() as conn:
            if not exact:
                exact = conn.execute(
                    text(
                        """
                        SELECT node_id
                        FROM catalog_object_staging
                        WHERE export_id = :export_id
                          AND object_type IN ('Source', 'Structure', 'Field')
                          AND is_graph_eligible
                        ORDER BY object_type, node_id
                        LIMIT 1
                        """
                    ),
                    {"export_id": args.export_id},
                ).scalar()
            if not full_path:
                full_path = conn.execute(
                    text(
                        """
                        SELECT path_full
                        FROM catalog_object_staging
                        WHERE export_id = :export_id
                          AND path_full IS NOT NULL
                          AND object_type IN ('Source', 'Structure', 'Field')
                          AND is_graph_eligible
                        ORDER BY length(path_full) DESC
                        LIMIT 1
                        """
                    ),
                    {"export_id": args.export_id},
                ).scalar()
            if not technical_name:
                technical_name = conn.execute(
                    text(
                        """
                        SELECT name_tech
                        FROM catalog_object_staging
                        WHERE export_id = :export_id
                          AND name_tech IS NOT NULL
                          AND object_type IN ('Structure', 'Field')
                          AND is_graph_eligible
                        ORDER BY length(name_tech) DESC
                        LIMIT 1
                        """
                    ),
                    {"export_id": args.export_id},
                ).scalar()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Query discovery from PostgreSQL failed: %s", exc)

    if exact:
        queries.insert(0, ("exact_id", str(exact)))
    if full_path:
        queries.append(("full_path", str(full_path)))
    if technical_name:
        queries.append(("technical_name", str(technical_name)))
    return queries


def request_search(api_base_url: str, search_path: str, query: str, limit: int) -> dict[str, Any]:
    params = urllib.parse.urlencode({"q": query, "limit": str(limit)})
    url = f"{api_base_url.rstrip('/')}/{search_path.strip('/')}?{params}"
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            elapsed_ms = (time.perf_counter() - started) * 1000
            payload = json.loads(body)
            results = payload.get("results") or []
            top_result = results[0] if results else None
            return {
                "ok": 200 <= int(response.status) < 300,
                "status_code": int(response.status),
                "latency_ms": round(elapsed_ms, 2),
                "result_count": int(payload.get("count") or len(results)),
                "top_result": {
                    "node_id": top_result.get("node_id"),
                    "label": top_result.get("label"),
                    "type": top_result.get("type"),
                    "path_full": top_result.get("path_full"),
                }
                if isinstance(top_result, dict)
                else None,
                "headers": {
                    "x_cache": response.headers.get("X-Cache"),
                    "x_graph_version": response.headers.get("X-Graph-Version"),
                    "server_timing": response.headers.get("Server-Timing"),
                },
                "error": None,
            }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": round(elapsed_ms, 2),
            "result_count": 0,
            "top_result": None,
            "headers": {},
            "error": str(exc),
        }


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return round(ordered[index], 2)


def summarize_case(case_type: str, query: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    cold = runs[0] if runs else None
    warm = runs[1:] if len(runs) > 1 else []
    warm_latencies = [float(row["latency_ms"]) for row in warm if row.get("ok")]
    cold_ok = bool(cold and cold.get("ok"))
    warm_ok = bool(warm) and all(row.get("ok") for row in warm)
    result_shapes_match = True
    if cold and warm:
        cold_count = cold.get("result_count")
        result_shapes_match = all(row.get("result_count") == cold_count for row in warm if row.get("ok"))
    graph_versions = [
        row.get("headers", {}).get("x_graph_version")
        for row in runs
        if row.get("ok")
    ]
    uses_fast_graph_version = bool(graph_versions) and all(
        version not in (None, "", "legacy")
        for version in graph_versions
    )
    return {
        "case_type": case_type,
        "query": query,
        "status": "ready" if cold_ok and warm_ok and result_shapes_match and uses_fast_graph_version else "blocked",
        "cold_latency_ms": cold.get("latency_ms") if cold else None,
        "warm_p50_ms": round(statistics.median(warm_latencies), 2) if warm_latencies else None,
        "warm_p95_ms": percentile(warm_latencies, 95),
        "result_shapes_match": result_shapes_match,
        "uses_fast_graph_version": uses_fast_graph_version,
        "graph_versions": sorted({str(version) for version in graph_versions if version is not None}),
        "cold": cold,
        "warm_runs": warm,
    }


def main() -> None:
    args = parse_args()
    queries = discover_queries(args)
    case_summaries: list[dict[str, Any]] = []
    for case_type, query in queries:
        runs = [request_search(args.api_base_url, args.search_path, query, args.limit)]
        for _ in range(max(0, args.runs)):
            runs.append(request_search(args.api_base_url, args.search_path, query, args.limit))
        case_summaries.append(summarize_case(case_type, query, runs))

    cold_latencies = [
        float(case["cold_latency_ms"])
        for case in case_summaries
        if case.get("status") == "ready" and case.get("cold_latency_ms") is not None
    ]
    warm_p95s = [
        float(case["warm_p95_ms"])
        for case in case_summaries
        if case.get("status") == "ready" and case.get("warm_p95_ms") is not None
    ]
    blockers = []
    if any(case["status"] != "ready" for case in case_summaries):
        blockers.append("One or more benchmark cases failed or returned inconsistent result shapes.")
    if cold_latencies and percentile(cold_latencies, 95) is not None and percentile(cold_latencies, 95) >= 1000:
        blockers.append("Cold search p95 is not below 1s.")
    if warm_p95s and percentile(warm_p95s, 95) is not None and percentile(warm_p95s, 95) >= 150:
        blockers.append("Cached/warm search p95 is not below 150ms.")
    if not cold_latencies:
        blockers.append("No successful search requests were measured.")
    if any(not case.get("uses_fast_graph_version") for case in case_summaries):
        blockers.append("One or more cases used legacy graph version instead of indexed fast search.")

    payload = {
        "export_id": args.export_id,
        "api_base_url": args.api_base_url,
        "search_path": args.search_path,
        "status": "blocked" if blockers else "ready",
        "acceptance": {
            "cold_p95_ms": percentile(cold_latencies, 95),
            "warm_p95_ms": percentile(warm_p95s, 95),
            "cold_p95_target_ms": 1000,
            "warm_p95_target_ms": 150,
        },
        "case_summaries": case_summaries,
        "blockers": blockers,
    }
    json_path = write_json_report(args.export_id, "fast_search_benchmark_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "fast_search_benchmark_report.md",
        "Migration V2 Fast Search Benchmark Report",
        [
            ("Status", f"`{payload['status']}`"),
            (
                "Acceptance",
                "\n".join(
                    [
                        f"- `cold_p95_ms`: {payload['acceptance']['cold_p95_ms']}",
                        f"- `warm_p95_ms`: {payload['acceptance']['warm_p95_ms']}",
                        f"- `cold_p95_target_ms`: {payload['acceptance']['cold_p95_target_ms']}",
                        f"- `warm_p95_target_ms`: {payload['acceptance']['warm_p95_target_ms']}",
                    ]
                ),
            ),
            (
                "Cases",
                "\n".join(
                    f"- `{case['case_type']}` `{case['query']}`: `{case['status']}` "
                    f"cold={case['cold_latency_ms']}ms warm_p95={case['warm_p95_ms']}ms "
                    f"versions={case['graph_versions']}"
                    for case in case_summaries
                )
                or "None.",
            ),
            (
                "Blockers",
                "\n".join(f"- {item}" for item in blockers) or "None.",
            ),
        ],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
