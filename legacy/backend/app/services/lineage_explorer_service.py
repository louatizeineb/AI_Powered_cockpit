from __future__ import annotations

import importlib.util
from pathlib import Path


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

    def search(self, q: str, limit: int = 20) -> dict:
        results = self.repo.search_entities(q=q, limit=limit)
        return {
            "query": q,
            "count": len(results),
            "results": results,
        }

    def neighbors(self, node_id: str, direction: str, limit: int = 50) -> dict | None:
        return self.repo.get_neighbors(node_id=node_id, direction=direction, limit=limit)
