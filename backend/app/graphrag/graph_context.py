from typing import Any


def build_graph_context(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build lightweight graph context for GraphRAG explanations.
    Later this can query Neo4j for neighbors, lineage, parents, and children.
    """
    return {
        "candidate_count": len(candidates),
        "candidates": candidates,
    }