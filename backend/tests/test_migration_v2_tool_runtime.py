from pathlib import Path

import pytest
from pydantic import ValidationError

from app.migration_v2.orchestration.tool_contracts import (
    BuildCandidateGraphInput,
    PublishGraphVersionInput,
    TOOL_INPUT_MODELS,
    validate_tool_input,
)
from app.migration_v2.agents.manifests import AGENT_MANIFESTS
from app.migration_v2.orchestration.tool_runtime import TOOL_SPECS


def test_phase5_tool_allowlist_is_complete():
    expected = {
        "register_export",
        "profile_export",
        "detect_schema_drift",
        "generate_mapping_plan",
        "preprocess_staging",
        "validate_staging",
        "populate_validation_queue",
        "build_candidate_graph",
        "generate_lineage_paths",
        "audit_candidate_graph",
        "run_search_benchmark",
        "publish_graph_version",
    }
    assert expected <= TOOL_SPECS.keys()


def test_tool_contract_rejects_unallowlisted_parameters():
    with pytest.raises(ValidationError):
        validate_tool_input(
            "build_candidate_graph",
            {"export_id": "export-1", "shell_command": "MATCH (n) DELETE n"},
        )


def test_tool_contract_builds_only_known_cli_arguments():
    payload = BuildCandidateGraphInput(
        export_id="export-1",
        env_config=Path("configs/migration_v2/local_env.yaml"),
        dry_run=True,
        batch_size=250,
    )
    arguments = payload.cli_arguments()
    assert arguments[:2] == ["--export-id", "export-1"]
    assert "--dry-run" in arguments
    assert "--batch-size" in arguments
    assert "--shell-command" not in arguments


def test_publish_defaults_to_dry_run_without_approver():
    payload = PublishGraphVersionInput(export_id="export-1")
    assert payload.dry_run is True
    assert payload.approved_by is None


def test_unknown_tool_has_no_dynamic_contract():
    with pytest.raises(ValueError, match="No typed input contract"):
        validate_tool_input("run_arbitrary_shell", {"export_id": "export-1"})


def test_executable_manifest_tools_have_specs_and_contracts():
    virtual_tools = {
        "read_schema_history",
        "read_validation_queue",
        "write_agent_proposals",
        "write_migration_report",
        "compare_baseline",
    }
    for manifest in AGENT_MANIFESTS.values():
        for tool_name in manifest.allowed_tools:
            if tool_name in virtual_tools:
                continue
            assert tool_name in TOOL_SPECS, f"{manifest.name} allows unspecced tool {tool_name}"
            assert tool_name in TOOL_INPUT_MODELS, f"{manifest.name} allows tool without typed input {tool_name}"


def test_no_migration_agent_manifest_grants_shell_access():
    for manifest in AGENT_MANIFESTS.values():
        assert "shell" not in manifest.allowed_tools
        assert "subprocess" not in manifest.allowed_tools
