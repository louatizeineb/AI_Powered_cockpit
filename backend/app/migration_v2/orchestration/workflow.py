from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.migration_v2.orchestration.state import MigrationGraphState, MigrationRunState


def initialize_workflow(state: MigrationGraphState) -> dict[str, Any]:
    validated = MigrationRunState.model_validate(state)
    return {
        "status": "running",
        "current_phase": validated.current_phase,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_phase1_graph(checkpointer: object | None = None):
    """Build the minimal durable graph; executable agents are added in Phase 3."""

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph is not installed. Install backend/requirements.txt before compiling the workflow."
        ) from exc

    builder = StateGraph(MigrationGraphState)
    builder.add_node("initialize_workflow", initialize_workflow)
    builder.add_edge(START, "initialize_workflow")
    builder.add_edge("initialize_workflow", END)
    return builder.compile(checkpointer=checkpointer)
