from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AgentCapability(StrEnum):
    READ_FILES = "read_files"
    READ_REPORTS = "read_reports"
    READ_STAGING = "read_staging"
    READ_GOVERNANCE = "read_governance"
    PROPOSE = "propose"
    WRITE_REGISTRY = "write_registry"
    WRITE_REPORTS = "write_reports"
    REQUEST_TOOL = "request_tool"
    REQUEST_APPROVAL = "request_approval"


class AgentManifest(BaseModel):
    """Versioned permissions and runtime limits for one orchestrated agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str
    mission: str
    capabilities: frozenset[AgentCapability]
    allowed_tools: tuple[str, ...] = ()
    write_scopes: tuple[str, ...] = ()
    output_schema: str
    requires_approval_for: tuple[str, ...] = ()
    max_llm_calls: int = Field(default=1, ge=0, le=1000)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    deterministic_fallback: bool = True

    @property
    def manifest_id(self) -> str:
        return f"{self.name}:{self.version}"


def _manifest(
    name: str,
    mission: str,
    tools: tuple[str, ...],
    output_schema: str,
    *,
    capabilities: frozenset[AgentCapability] | None = None,
    write_scopes: tuple[str, ...] = (),
    approval: tuple[str, ...] = (),
    max_llm_calls: int = 1,
) -> AgentManifest:
    return AgentManifest(
        name=name,
        version="1.0.0",
        mission=mission,
        capabilities=capabilities
        or frozenset(
            {
                AgentCapability.READ_REPORTS,
                AgentCapability.PROPOSE,
                AgentCapability.REQUEST_TOOL,
            }
        ),
        allowed_tools=tools,
        write_scopes=write_scopes,
        output_schema=output_schema,
        requires_approval_for=approval,
        max_llm_calls=max_llm_calls,
    )


AGENT_MANIFESTS = {
    manifest.name: manifest
    for manifest in (
        _manifest(
            "ExportIntakeAgent",
            "Detect, fingerprint, and register a metadata export.",
            ("register_export",),
            "ExportIntakeResult",
            capabilities=frozenset(
                {
                    AgentCapability.READ_FILES,
                    AgentCapability.WRITE_REGISTRY,
                    AgentCapability.REQUEST_APPROVAL,
                }
            ),
            write_scopes=("migration_export_run", "migration_raw_file"),
            approval=("ambiguous_export_identity",),
            max_llm_calls=0,
        ),
        _manifest(
            "SchemaIntelligenceAgent",
            "Profile raw tables and columns and propose schema observations.",
            ("profile_export", "read_schema_history", "build_schema_intelligence_kg"),
            "SchemaIntelligenceResult",
            write_scopes=("migration_column_profile", "governance_kg_proposals"),
            approval=("conflicting_schema_identity",),
            max_llm_calls=20,
        ),
        _manifest(
            "MappingOntologyAgent",
            "Compare schema evidence with contracts and propose canonical mappings.",
            ("detect_schema_drift", "generate_mapping_plan", "read_schema_history"),
            "MappingProposalResult",
            write_scopes=("migration_mapping_decision",),
            approval=("unknown_required_column", "inferred_mapping", "contract_change"),
            max_llm_calls=20,
        ),
        _manifest(
            "PreprocessingAgent",
            "Request deterministic canonical staging generation.",
            ("preprocess_staging",),
            "PreprocessingResult",
            approval=("repair", "exclusion"),
            max_llm_calls=0,
        ),
        _manifest(
            "ValidationAgent",
            "Run deterministic staging and graph constraints.",
            ("validate_staging", "populate_validation_queue", "resolve_structural_parity", "build_conditional_projection"),
            "ValidationResult",
            write_scopes=("migration_validation_finding", "migration_validation_queue"),
            max_llm_calls=0,
        ),
        _manifest(
            "ValidationGuardianAgent",
            "Propose evidence-backed governance actions for unresolved findings.",
            ("read_validation_queue", "write_agent_proposals", "evaluate_validation_agent"),
            "AgentRunResult",
            write_scopes=("migration_agent_run", "migration_agent_proposal", "migration_agent_eval_run"),
            approval=("apply_proposal",),
            max_llm_calls=200,
        ),
        _manifest(
            "GraphBuildAgent",
            "Request a deterministic candidate graph build from approved staging.",
            ("build_candidate_graph", "enforce_trusted_graph_projection", "generate_lineage_paths"),
            "GraphBuildResult",
            approval=("candidate_graph_activation",),
            max_llm_calls=0,
        ),
        _manifest(
            "AuditComparisonAgent",
            "Audit candidate quality and compare source, v0, and v2 evidence.",
            ("audit_candidate_graph", "compare_baseline"),
            "AuditResult",
            max_llm_calls=5,
        ),
        _manifest(
            "PublishGuardianAgent",
            "Evaluate publish gates and request controlled activation or rollback.",
            ("build_conditional_projection", "activate_candidate_search", "run_search_benchmark", "publish_graph_version"),
            "PublishRecommendation",
            approval=("publish", "rollback"),
            max_llm_calls=2,
        ),
        _manifest(
            "ReportAgent",
            "Assemble technical and executive evidence packets.",
            ("write_migration_report",),
            "MigrationReportResult",
            capabilities=frozenset(
                {
                    AgentCapability.READ_REPORTS,
                    AgentCapability.WRITE_REPORTS,
                }
            ),
            write_scopes=("reports/migration_v2",),
            max_llm_calls=2,
        ),
    )
}


def get_agent_manifest(name: str) -> AgentManifest:
    try:
        return AGENT_MANIFESTS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown migration agent manifest: {name}") from exc
