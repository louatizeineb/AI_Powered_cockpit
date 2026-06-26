from __future__ import annotations

from app.migration_v2.graph.graph_builder import build_candidate_graph
from app.migration_v2.profiling.schema_drift_detector import detect_schema_drift


def test_graph_builder_module_delegates_to_allowlisted_runtime():
    result = build_candidate_graph("export-1", env_config="configs/migration_v2/local_env.yaml")
    assert result["status"] == "delegated_to_allowlisted_tool_runtime"
    assert result["agent_name"] == "GraphBuildAgent"
    assert result["tool_name"] == "build_candidate_graph"


def test_schema_drift_detector_returns_real_status():
    result = detect_schema_drift(
        {
            "contract_version": "1.0",
            "tables": {
                "raw_customer": {
                    "required_columns": ["customer_id"],
                    "columns": {"customer_id": "customer_id", "name": "name"},
                }
            },
        },
        {"tables": {"raw_customer": ["customer_id"]}},
    )
    assert result["status"] == "ready"
    assert result["tables"]["raw_customer"]["missing_mapped_columns"] == ["name"]
    assert result["missing_required_columns"] == []
