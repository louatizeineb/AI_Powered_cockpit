from __future__ import annotations

import importlib.util
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from app.common.cache import get_json, set_json
from app.config import get_settings


def _load_repository_class():
    path = Path(__file__).resolve().parents[1] / "repositories" / "lineage_explorer_repository.py"
    spec = importlib.util.spec_from_file_location("lineage_explorer_repository_runtime", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load lineage explorer repository")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LineageExplorerRepository


LineageExplorerRepository = _load_repository_class()


class LineageExplorerService:
    def __init__(self) -> None:
        self.repo = LineageExplorerRepository()
        self.settings = get_settings()
        self.last_metadata: dict[str, str] = {}

    def search(self, q: str, limit: int = 20) -> dict:
        return self._cached(
            namespace="search",
            key_parts=[q.strip().lower(), str(limit)],
            ttl_seconds=self.settings.lineage_search_cache_ttl_seconds,
            loader=lambda: self._search(q=q, limit=limit),
        )

    def neighbors(self, node_id: str, direction: str, limit: int = 50) -> dict | None:
        return self._cached(
            namespace="neighbors",
            key_parts=[node_id, direction, str(limit)],
            ttl_seconds=self.settings.lineage_expansion_cache_ttl_seconds,
            loader=lambda: self.repo.get_neighbors(node_id=node_id, direction=direction, limit=limit),
        )

    def source_context(
        self,
        node_id: str,
        catalog_offset: int = 0,
        catalog_limit: int = 500,
        consumer_limit: int = 300,
    ) -> dict | None:
        return self._cached(
            namespace="source-context",
            key_parts=[node_id, str(catalog_offset), str(catalog_limit), str(consumer_limit)],
            ttl_seconds=self.settings.lineage_expansion_cache_ttl_seconds,
            loader=lambda: self.repo.get_source_context(
                node_id=node_id,
                catalog_offset=catalog_offset,
                catalog_limit=catalog_limit,
                consumer_limit=consumer_limit,
            ),
        )

    def _search(self, q: str, limit: int) -> dict:
        results = self.repo.search_entities(q=q, limit=limit)
        return {
            "query": q,
            "count": len(results),
            "results": results,
        }

    def _cached(
        self,
        namespace: str,
        key_parts: list[str],
        ttl_seconds: int,
        loader,
    ) -> Any:
        started = perf_counter()
        graph_version = self.repo.active_graph_version()
        digest = sha256("\x1f".join(key_parts).encode("utf-8")).hexdigest()
        key = f"lineage:{graph_version}:{namespace}:{digest}"
        cached = get_json(key)
        if cached is not None:
            self._set_metadata(graph_version, "hit", started)
            return cached

        value = loader()
        cache_status = "stored" if value is not None and set_json(key, value, ttl_seconds) else "bypass"
        self._set_metadata(graph_version, cache_status, started)
        return value

    def _set_metadata(self, graph_version: str, cache_status: str, started: float) -> None:
        timings = getattr(self.repo, "last_timings", {})
        parts = [f"{name};dur={duration:.2f}" for name, duration in timings.items()]
        parts.append(f"total;dur={(perf_counter() - started) * 1000:.2f}")
        self.last_metadata = {
            "cache": cache_status,
            "graph_version": graph_version,
            "server_timing": ", ".join(parts),
        }
