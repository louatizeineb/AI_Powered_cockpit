from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_id: str = Field(min_length=1)

    def cli_arguments(self) -> list[str]:
        arguments: list[str] = []
        for name, value in self.model_dump(exclude_none=True).items():
            flag = "--" + name.replace("_", "-")
            if isinstance(value, bool):
                if value:
                    arguments.append(flag)
            else:
                arguments.extend((flag, str(value)))
        return arguments


class RegisterExportInput(ToolInput):
    export_path: Path
    contract: Path | None = None
    skip_row_count: bool = False


class ProfileExportInput(ToolInput):
    sample_size: Annotated[int, Field(ge=1, le=1000)] = 10


class ContractInput(ToolInput):
    contract: Path


class ValidateStagingInput(ToolInput):
    contract: Path | None = None


class EnvironmentInput(ToolInput):
    env_config: Path | None = None


class BuildCandidateGraphInput(EnvironmentInput):
    batch_size: Annotated[int, Field(ge=1, le=100_000)] = 1000
    dry_run: bool = False
    force: bool = False
    clear_first: bool = False
    clear_batch_size: Annotated[int, Field(ge=1, le=100_000)] = 5000
    skip_usage_resolver: bool = False
    usage_dataset_path_limit: Annotated[int, Field(ge=0, le=100_000)] = 0


class GenerateLineagePathsInput(EnvironmentInput):
    batch_size: Annotated[int, Field(ge=1, le=100_000)] = 1000
    max_paths_per_family: Annotated[int, Field(ge=0)] = 0


class TrustedGraphProjectionInput(EnvironmentInput):
    batch_size: Annotated[int, Field(ge=1, le=100_000)] = 1000
    dry_run: bool = False


class SearchBenchmarkInput(EnvironmentInput):
    api_base_url: str = "http://127.0.0.1:8001"
    limit: Annotated[int, Field(ge=1, le=200)] = 20
    runs: Annotated[int, Field(ge=1, le=100)] = 5
    exact_node_id: str | None = None
    full_path: str | None = None
    technical_name: str | None = None
    search_path: str = "/lineage/explorer/search"


class PopulateValidationQueueInput(EnvironmentInput):
    approve_proposed_quarantine: bool = False
    approved_by: str | None = None
    approval_rationale: str = ""


class PublishGraphVersionInput(EnvironmentInput):
    approved_by: str | None = None
    dry_run: bool = True


class ConditionalPublishInput(EnvironmentInput):
    policy_version: str = "conditional-publish-v1"
    decided_by: str = "deterministic_policy_engine"


class StructuralParityInput(EnvironmentInput):
    apply: bool = False
    approved_by: str = "deterministic_structural_parity_verifier"


class AgentEvaluationInput(EnvironmentInput):
    limit: Annotated[int, Field(ge=1, le=1000)] = 100
    issue_type: str | None = None
    mode: Literal["latest_proposals", "deterministic"] = "latest_proposals"
    bootstrap_from_queue: bool = True


class SchemaIntelligenceInput(EnvironmentInput):
    contract: Path | None = None
    source_system: str = "datagalaxy_athena"
    batch_size: Annotated[int, Field(ge=1, le=100_000)] = 500
    dry_run: bool = False


TOOL_INPUT_MODELS: dict[str, type[ToolInput]] = {
    "register_export": RegisterExportInput,
    "profile_export": ProfileExportInput,
    "detect_schema_drift": ContractInput,
    "generate_mapping_plan": ToolInput,
    "preprocess_staging": ContractInput,
    "validate_staging": ValidateStagingInput,
    "populate_validation_queue": PopulateValidationQueueInput,
    "build_conditional_projection": ConditionalPublishInput,
    "build_candidate_graph": BuildCandidateGraphInput,
    "generate_lineage_paths": GenerateLineagePathsInput,
    "audit_candidate_graph": EnvironmentInput,
    "run_search_benchmark": SearchBenchmarkInput,
    "activate_candidate_search": EnvironmentInput,
    "publish_graph_version": PublishGraphVersionInput,
    "build_schema_intelligence_kg": SchemaIntelligenceInput,
    "resolve_structural_parity": StructuralParityInput,
    "enforce_trusted_graph_projection": TrustedGraphProjectionInput,
    "evaluate_validation_agent": AgentEvaluationInput,
}


def validate_tool_input(tool_name: str, payload: ToolInput | dict[str, Any]) -> ToolInput:
    try:
        model = TOOL_INPUT_MODELS[tool_name]
    except KeyError as exc:
        raise ValueError(f"No typed input contract exists for tool {tool_name!r}.") from exc
    if isinstance(payload, model):
        return payload
    raw = payload.model_dump(mode="python") if isinstance(payload, BaseModel) else payload
    return model.model_validate(raw)
