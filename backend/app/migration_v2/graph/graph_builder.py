from __future__ import annotations

from typing import Any


def build_candidate_graph(export_id: str, *, env_config: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    """Describe the controlled graph-build tool invocation.

    Candidate graph writes are intentionally executed through AllowlistedToolRuntime
    so permissions, typed payloads, artifacts, and database effects are recorded.
    """

    payload: dict[str, Any] = {"export_id": export_id, "dry_run": dry_run}
    if env_config:
        payload["env_config"] = env_config
    return {
        "export_id": export_id,
        "status": "delegated_to_allowlisted_tool_runtime",
        "agent_name": "GraphBuildAgent",
        "tool_name": "build_candidate_graph",
        "payload": payload,
    }
