from __future__ import annotations

import json
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import text

from app.migration_v2.agents.base import call_chat_llm, compact_json, llm_config_status, parse_json_object
from app.migration_v2.agents.execution import AgentContext, ExecutableAgentResult, MappingProposal
from app.migration_v2.agents.manifests import get_agent_manifest
from app.migration_v2.agents.persistence import AgentExecutionRepository


AGENT_ROLE = get_agent_manifest("MappingOntologyAgent")
ALLOWED_ACTIONS = {
    "keep_contract_missing",
    "deprecate_contract_column",
    "map_to_observed_column",
    "needs_human",
}
SYSTEM_PROMPT = """
You are MappingOntologyAgent for a governed enterprise metadata migration.
Review one unresolved schema mapping and return strict JSON only with:
proposed_action, proposed_canonical_field, confidence, rationale,
missing_evidence, human_question.

Allowed proposed_action values:
keep_contract_missing, deprecate_contract_column, map_to_observed_column, needs_human.

Rules:
- You propose only; never alter the contract or graph.
- A missing column in one export is not enough evidence to deprecate it automatically.
- map_to_observed_column is allowed only when an explicit candidate is supplied.
- Prefer needs_human when historical exports, source ownership, or semantic evidence are absent.
""".strip()


def run(context: AgentContext) -> ExecutableAgentResult:
    llm_available, llm_reason = llm_config_status()
    if context.require_llm and not llm_available:
        raise RuntimeError(f"Mapping LLM is required but unavailable: {llm_reason}")
    mode = "llm" if llm_available else "deterministic_fallback"
    persistence = AgentExecutionRepository(context.engine)
    agent_run_id = persistence.start(
        export_id=context.state.export_id,
        workflow_run_id=context.state.run_id,
        agent_name=AGENT_ROLE.name,
        mode=mode,
    )
    tools_used: list[str] = []
    with context.engine.connect() as conn:
        decision_count = int(
            conn.execute(
                text("SELECT count(*) FROM migration_mapping_decision WHERE export_id = :export_id"),
                {"export_id": context.state.export_id},
            ).scalar_one()
        )
    if decision_count == 0 or context.refresh_tools:
        context.tool_runtime.execute(
            agent_name=AGENT_ROLE.name,
            tool_name="detect_schema_drift",
            payload={"export_id": context.state.export_id, "contract": context.contract_path},
            refresh=context.refresh_tools,
        )
        context.tool_runtime.execute(
            agent_name=AGENT_ROLE.name,
            tool_name="generate_mapping_plan",
            payload={"export_id": context.state.export_id},
            refresh=context.refresh_tools,
        )
        tools_used.extend(["detect_schema_drift", "generate_mapping_plan"])

    unresolved = fetch_unresolved(context.engine, context.state.export_id)
    persisted = fetch_pending_proposals(context.engine, context.state.run_id)
    unresolved_keys = {
        (str(item["raw_table_name"]), str(item["raw_column_name"])) for item in unresolved
    }
    persisted_keys = {
        (proposal.raw_table_name, proposal.raw_column_name) for proposal in persisted
    }
    if unresolved_keys and unresolved_keys == persisted_keys:
        result = ExecutableAgentResult(
            export_id=context.state.export_id,
            workflow_run_id=context.state.run_id,
            agent_name=AGENT_ROLE.name,
            status="waiting_approval",
            mode="persisted_proposal_reuse",
            summary={
                "reviewed_count": len(unresolved),
                "decision_count": decision_count,
                "unresolved_mapping_count": len(unresolved),
            },
            proposals=persisted,
            tools_used=tools_used,
        )
        persistence.finish(agent_run_id, result)
        return result

    proposals: list[MappingProposal] = []
    errors: list[str] = []
    model_name: str | None = None
    llm_calls = 0
    fallbacks = 0
    llm_disabled = False
    for item in unresolved:
        candidates = candidate_columns(context.engine, context.state.export_id, item)
        proposal = None
        if llm_available and not llm_disabled:
            try:
                raw, model_name = call_chat_llm(
                    SYSTEM_PROMPT,
                    "Review this mapping evidence:\n" + compact_json({**item, "candidate_columns": candidates}),
                )
                llm_calls += 1
                proposal = proposal_from_model(item, candidates, parse_json_object(raw), raw)
            except Exception as exc:  # noqa: BLE001
                if context.require_llm:
                    errors.append(str(exc))
                    break
                llm_disabled = True
                mode = "llm_unavailable_deterministic_fallback"
        if proposal is None:
            proposal = deterministic_proposal(item, candidates)
            fallbacks += 1
        proposals.append(enforce_mapping_guardrails(proposal, item))

    status = "completed_with_errors" if errors else ("waiting_approval" if proposals else "completed")
    result = ExecutableAgentResult(
        export_id=context.state.export_id,
        workflow_run_id=context.state.run_id,
        agent_name=AGENT_ROLE.name,
        status=status,
        mode=mode,
        model_name=model_name,
        summary={
            "reviewed_count": len(unresolved),
            "decision_count": decision_count,
            "unresolved_mapping_count": len(unresolved),
        },
        proposals=proposals,
        tools_used=tools_used,
        errors=errors,
        llm_call_count=llm_calls,
        fallback_count=fallbacks,
    )
    persistence.insert_mapping_proposals(
        export_id=context.state.export_id,
        workflow_run_id=context.state.run_id,
        agent_run_id=agent_run_id,
        proposals=proposals,
    )
    persistence.finish(agent_run_id, result)
    return result


def fetch_unresolved(engine, export_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT raw_table_name, raw_column_name, canonical_field,
                       decision_type, confidence, requires_human_approval,
                       rationale, evidence
                FROM migration_mapping_decision
                WHERE export_id = :export_id AND requires_human_approval = true
                ORDER BY raw_table_name, raw_column_name
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
    return [dict(row) for row in rows]


def fetch_pending_proposals(engine, workflow_run_id: str) -> list[MappingProposal]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT ON (raw_table_name, raw_column_name)
                       raw_table_name, raw_column_name, current_canonical_field,
                       proposed_canonical_field, proposed_action, confidence,
                       rationale, missing_evidence, human_question, candidate_columns,
                       guardrail_actions, raw_model_response
                FROM migration_schema_mapping_proposal
                WHERE workflow_run_id = CAST(:workflow_run_id AS uuid)
                  AND status = 'pending'
                ORDER BY raw_table_name, raw_column_name, created_at DESC
                """
            ),
            {"workflow_run_id": workflow_run_id},
        ).mappings().all()
    return [
        MappingProposal(
            raw_table_name=str(row["raw_table_name"]),
            raw_column_name=str(row["raw_column_name"]),
            current_canonical_field=row.get("current_canonical_field"),
            proposed_canonical_field=row.get("proposed_canonical_field"),
            proposed_action=str(row["proposed_action"]),
            confidence=float(row["confidence"]),
            rationale=str(row["rationale"]),
            missing_evidence=list(row.get("missing_evidence") or []),
            human_question=str(row.get("human_question") or ""),
            candidate_columns=list(row.get("candidate_columns") or []),
            guardrail_actions=list(row.get("guardrail_actions") or []),
            raw_model_response=str(row.get("raw_model_response") or ""),
        )
        for row in rows
    ]


def candidate_columns(engine, export_id: str, item: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_name
                FROM migration_column_profile
                WHERE export_id = :export_id AND raw_table_name = :raw_table_name
                """
            ),
            {"export_id": export_id, "raw_table_name": item["raw_table_name"]},
        ).scalars().all()
    expected = str(item.get("raw_column_name") or "")
    scored = [
        {"raw_column_name": str(name), "name_similarity": round(fuzz.WRatio(expected, str(name)) / 100.0, 4)}
        for name in rows
        if str(name) != expected
    ]
    return sorted(scored, key=lambda row: row["name_similarity"], reverse=True)[:limit]


def deterministic_proposal(item: dict[str, Any], candidates: list[dict[str, Any]]) -> MappingProposal:
    strong = next((row for row in candidates if row["name_similarity"] >= 0.92), None)
    if strong:
        action = "map_to_observed_column"
        proposed = strong["raw_column_name"]
        confidence = strong["name_similarity"]
        rationale = "A strongly similar observed raw column exists, but the mapping still requires approval."
    else:
        action = "keep_contract_missing"
        proposed = item.get("canonical_field")
        confidence = 0.72
        rationale = "The column is contract-declared but absent from this export; retain it as missing until historical or owner evidence is available."
    return MappingProposal(
        raw_table_name=str(item["raw_table_name"]),
        raw_column_name=str(item["raw_column_name"]),
        current_canonical_field=item.get("canonical_field"),
        proposed_canonical_field=proposed,
        proposed_action=action,
        confidence=confidence,
        rationale=rationale,
        missing_evidence=["historical export observations", "source owner confirmation"],
        human_question="Should this contract column remain expected, be deprecated, or map to an observed replacement?",
        candidate_columns=candidates,
    )


def proposal_from_model(
    item: dict[str, Any], candidates: list[dict[str, Any]], payload: dict[str, Any], raw: str
) -> MappingProposal:
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    missing = payload.get("missing_evidence") or []
    if isinstance(missing, str):
        missing = [missing]
    return MappingProposal(
        raw_table_name=str(item["raw_table_name"]),
        raw_column_name=str(item["raw_column_name"]),
        current_canonical_field=item.get("canonical_field"),
        proposed_canonical_field=payload.get("proposed_canonical_field"),
        proposed_action=str(payload.get("proposed_action") or "needs_human"),
        confidence=max(0.0, min(1.0, confidence)),
        rationale=str(payload.get("rationale") or "No rationale provided."),
        missing_evidence=[str(value) for value in missing],
        human_question=str(payload.get("human_question") or ""),
        candidate_columns=candidates,
        raw_model_response=raw,
    )


def enforce_mapping_guardrails(proposal: MappingProposal, item: dict[str, Any]) -> MappingProposal:
    if proposal.proposed_action not in ALLOWED_ACTIONS:
        proposal.guardrail_actions.append("Unknown action downgraded to needs_human.")
        proposal.proposed_action = "needs_human"
    candidate_names = {row["raw_column_name"] for row in proposal.candidate_columns}
    if proposal.proposed_action == "map_to_observed_column" and proposal.proposed_canonical_field not in candidate_names:
        proposal.guardrail_actions.append("Mapping target was not an observed candidate; downgraded to needs_human.")
        proposal.proposed_action = "needs_human"
        proposal.proposed_canonical_field = item.get("canonical_field")
    if proposal.confidence < 0.5 and proposal.proposed_action != "needs_human":
        proposal.guardrail_actions.append("Low-confidence proposal downgraded to needs_human.")
        proposal.proposed_action = "needs_human"
    return proposal
