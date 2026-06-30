from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.migration_v2.agents.base import (
    AgentProposal,
    AgentRunResult,
    call_chat_llm,
    compact_json,
    enforce_guardrails,
    llm_config_status,
    llm_settings,
    parse_json_object,
    proposal_from_payload,
)
from app.migration_v2.agents.evidence_retriever import retrieve_evidence_packet
from app.migration_v2.agents.reasoning import build_evidence_plan, evidence_plan_to_dict


AGENT_ROLE = {
    "name": "ValidationGuardianAgent",
    "mission": "Review migration validation queue issues and propose safe publish governance actions.",
    "tools": ["migration_validation_queue", "validation_queue_report", "agent_proposals"],
    "requires_human_approval": "always before queue decisions affect publish",
}

SYSTEM_PROMPT = """
You are ValidationGuardianAgent for an enterprise metadata knowledge graph migration.
You review exactly one validation queue issue and return strict JSON only.

Allowed proposed_policy values:
accept, quarantine, exclude, repair, needs_human, block.

Required JSON fields:
issue_id, proposed_policy, confidence, rationale, missing_evidence, human_question.

Safety rules:
- You propose only. You never approve publish.
- proposed_policy is a review recommendation, not a mutation. Do not choose needs_human solely because human
  approval is required; use human_question to ask for that confirmation.
- Do not mark repairs as complete.
- HAS_FIELD count parity issues must stay repair until the exact missing edge is identified.
- IMPLEMENTS parity issues must stay needs_human or repair until edge-level diff exists.
- High severity issues should not be accepted without concrete evidence.
- Prefer quarantine for bounded lineage endpoints that are unsafe for trusted hierarchy/search but traceable.

Grounding rules:
- Use retrieved_evidence when available.
- Treat governance_memory as approved human precedent. If it has a recommended_policy with confidence >= 0.65
  and no safety rule blocks it, your proposed_policy should normally match that recommendation.
- In rationale, cite concrete evidence categories such as object rows, relationship neighbors, lineage examples,
  similar approved decisions, schema column matches, or provenance events.
- If retrieved evidence is weak or empty, say exactly which evidence is missing instead of sounding certain.
- Use evidence_plan as your reasoning contract. Do not invent SQL, hidden tools, or repair actions outside that plan.
- If a repair is possible, explain which planned read-only query must prove it before any change proposal is safe.
""".strip()


def run(
    engine: Engine,
    export_id: str,
    *,
    limit: int = 25,
    issue_type: str | None = None,
    require_llm: bool = False,
) -> AgentRunResult:
    settings = llm_settings()
    llm_available, llm_reason = llm_config_status(settings)
    if require_llm and not llm_available:
        raise RuntimeError(f"LLM is required, but Azure/OpenAI chat config is unavailable: {llm_reason}.")

    items = fetch_queue_items(engine, export_id, limit=limit, issue_type=issue_type)
    llm_call_limit = max(0, min(len(items), int(settings.llm_run_max_calls or 0)))
    proposals: list[AgentProposal] = []
    evidence_plans = []
    errors: list[str] = []
    llm_call_count = 0
    fallback_count = 0
    model_name: str | None = None
    mode = "llm" if llm_available else "deterministic_fallback"
    llm_disabled_for_run = False

    for item in items:
        item["export_id"] = export_id
        item["evidence_plan"] = build_evidence_plan(item)
        item["retrieved_evidence"] = retrieve_evidence_packet(engine, export_id, item)
        evidence_plans.append(item["evidence_plan"])
        proposal: AgentProposal | None = None
        if llm_available and not llm_disabled_for_run and llm_call_count < llm_call_limit:
            try:
                raw_response, model_name = call_chat_llm(SYSTEM_PROMPT, user_prompt_for_item(item))
                llm_call_count += 1
                payload = parse_json_object(raw_response)
                payload.setdefault("issue_id", item["issue_id"])
                payload.setdefault("issue_type", item.get("issue_type"))
                proposal = proposal_from_payload(payload, raw_model_response=raw_response)
            except Exception as exc:  # noqa: BLE001
                if require_llm:
                    errors.append(f"{item['issue_id']}: llm_failed: {exc}")
                    break
                llm_disabled_for_run = True
                mode = "llm_unavailable_deterministic_fallback"
        elif llm_available and llm_call_count >= llm_call_limit:
            mode = "llm_budget_limited_deterministic_fallback"

        if proposal is None:
            proposal = deterministic_proposal(item)
            fallback_count += 1

        proposals.append(enforce_guardrails(proposal, item))

    status = "completed_with_errors" if errors else "completed"
    return AgentRunResult(
        export_id=export_id,
        agent_name=AGENT_ROLE["name"],
        mode=mode,
        model_name=model_name,
        reviewed_count=len(items),
        proposal_count=len(proposals),
        llm_call_count=llm_call_count,
        fallback_count=fallback_count,
        proposals=proposals,
        evidence_plans=evidence_plans,
        errors=errors,
        status=status,
    )


def fetch_queue_items(
    engine: Engine,
    export_id: str,
    *,
    limit: int,
    issue_type: str | None,
) -> list[dict[str, Any]]:
    where = [
        "export_id = :export_id",
        "queue_status NOT IN ('approved', 'resolved')",
    ]
    params: dict[str, Any] = {"export_id": export_id, "limit": limit}
    if issue_type:
        where.append("issue_type = :issue_type")
        params["issue_type"] = issue_type
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT issue_id, issue_type, entity_kind, node_id, relationship_type,
                       severity, confidence, publish_policy, queue_status, source_report,
                       source_decision_status, proposed_action, rationale, evidence
                FROM migration_validation_queue
                WHERE {' AND '.join(where)}
                ORDER BY
                    CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                    issue_type,
                    issue_id
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
    return [normalize_item(dict(row)) for row in rows]


def normalize_item(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence")
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            evidence = {"raw_evidence": evidence}
    row["evidence"] = evidence or {}
    return row


def user_prompt_for_item(item: dict[str, Any]) -> str:
    settings = llm_settings()
    packet = {
        "issue": {
            key: item.get(key)
            for key in [
                "issue_id",
                "issue_type",
                "entity_kind",
                "node_id",
                "relationship_type",
                "severity",
                "publish_policy",
                "queue_status",
                "source_report",
                "source_decision_status",
                "proposed_action",
                "rationale",
            ]
        },
        "governance_memory": governance_memory_summary(item.get("retrieved_evidence") or {}),
        "evidence": compact_evidence(item.get("evidence") or {}),
        "evidence_plan": compact_evidence_plan(item.get("evidence_plan")),
        "retrieved_evidence": compact_retrieved_evidence(item.get("retrieved_evidence") or {}),
        "path_pattern": classify_path_pattern((item.get("evidence") or {}).get("paths") or []),
    }
    return "Review this validation queue issue and return the required JSON.\n" + compact_json(
        packet,
        max_chars=max(2000, int(settings.llm_max_prompt_chars or 8000)),
    )


def compact_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "object_type",
        "canonical_role",
        "observed_roles",
        "conflict_fields",
        "parent_node_ids",
        "source_tables",
        "path_full",
        "paths",
        "labels",
        "technical_names",
        "relationship_count",
        "child_count",
        "incoming_context_types",
        "outgoing_context_types",
        "baseline_value",
        "v2_value",
        "delta_value",
        "required_action",
        "inverse_relationship_type",
        "raw_link_types",
    ]
    payload = {key: evidence.get(key) for key in keys if key in evidence}
    for key in ("paths", "labels", "technical_names", "parent_node_ids", "source_tables", "observed_roles"):
        if isinstance(payload.get(key), list):
            payload[key] = payload[key][:5]
    return payload


def governance_memory_summary(packet: dict[str, Any]) -> dict[str, Any]:
    decisions = packet.get("similar_decisions") or []
    policy, confidence = policy_from_similar_decisions(decisions)
    examples = []
    for decision in decisions[:5]:
        policy_value = str(decision.get("publish_policy") or "").lower()
        status = str(decision.get("queue_status") or "").lower()
        if status not in {"approved", "resolved"} or not policy_value:
            continue
        examples.append(
            {
                "issue_id": decision.get("issue_id"),
                "issue_type": decision.get("issue_type"),
                "relationship_type": decision.get("relationship_type"),
                "publish_policy": policy_value,
                "queue_status": status,
                "confidence": decision.get("confidence"),
                "rationale": decision.get("rationale"),
            }
        )
    return {
        "recommended_policy": policy,
        "memory_confidence": round(confidence, 4),
        "approved_or_resolved_count": len(examples),
        "examples": examples,
        "instruction": "Use recommended_policy as approved precedent when memory_confidence is at least 0.65; still ask a human to confirm before mutation.",
    }


def compact_evidence_plan(plan: Any) -> dict[str, Any]:
    if plan is None:
        return {}
    payload = evidence_plan_to_dict(plan)
    return {
        "objective": payload.get("objective"),
        "required_evidence": (payload.get("required_evidence") or [])[:10],
        "planned_tools": payload.get("planned_tools") or [],
        "planned_queries": [
            {
                "query_id": query.get("query_id"),
                "purpose": query.get("purpose"),
                "sql": query.get("sql"),
                "safety": query.get("safety"),
            }
            for query in (payload.get("planned_queries") or [])[:6]
        ],
        "repair_strategy": payload.get("repair_strategy"),
        "risk_flags": payload.get("risk_flags") or [],
    }


def compact_retrieved_evidence(packet: dict[str, Any]) -> dict[str, Any]:
    settings = llm_settings()
    return {
        "retrieval_version": packet.get("retrieval_version"),
        "counts": packet.get("counts") or {},
        "object_rows": (packet.get("object_rows") or [])[: max(0, int(settings.llm_rag_object_rows or 0))],
        "relationship_neighbors": (packet.get("relationship_neighbors") or [])[: max(0, int(settings.llm_rag_relationship_neighbors or 0))],
        "lineage_examples": (packet.get("lineage_examples") or [])[: max(0, int(settings.llm_rag_lineage_examples or 0))],
        "similar_decisions": (packet.get("similar_decisions") or [])[: max(0, int(settings.llm_rag_similar_decisions or 0))],
        "schema_columns": (packet.get("schema_columns") or [])[: max(0, int(settings.llm_rag_schema_columns or 0))],
        "provenance_events": (packet.get("provenance_events") or [])[: max(0, int(settings.llm_rag_provenance_events or 0))],
    }


def deterministic_proposal(item: dict[str, Any]) -> AgentProposal:
    issue_id = str(item["issue_id"])
    issue_type = str(item.get("issue_type") or "")
    relationship_type = str(item.get("relationship_type") or "")
    evidence = item.get("evidence") or {}
    retrieved = item.get("retrieved_evidence") or {}
    counts = retrieved.get("counts") or {}
    grounding = grounding_phrase(
        counts,
        evidence=evidence,
        issue_type=issue_type,
        relationship_type=relationship_type,
    )
    memory_policy, memory_confidence = policy_from_similar_decisions(retrieved.get("similar_decisions") or [])

    if relationship_type == "Relationships":
        return AgentProposal(
            issue_id=issue_id,
            issue_type=issue_type,
            proposed_policy="accept",
            confidence=0.75,
            rationale=f"v0 exposes an aggregate relationship total while v2 exposes typed relationships; accept as comparator limitation after documentation. {grounding}",
            missing_evidence=["baseline edge/type breakdown"],
            human_question="Do you accept aggregate-only v0 relationship parity as a comparator limitation?",
            fallback_used=True,
        )
    if relationship_type == "HAS_FIELD":
        if str(evidence.get("edge_level_diff") or "").lower() == "zero_real_missing_edges" and int(evidence.get("legacy_blank_rows") or 0) > 0:
            return AgentProposal(
                issue_id=issue_id,
                issue_type=issue_type,
                proposed_policy="accept",
                confidence=0.92,
                rationale=f"Edge-level comparison found zero real missing HAS_FIELD identities; the v0 delta is explained by {int(evidence.get('legacy_blank_rows') or 0)} fully blank legacy row(s), so this is an explained baseline-quality exception. {grounding}",
                missing_evidence=[],
                human_question="Do you accept the verified blank-baseline HAS_FIELD exception as non-blocking?",
                fallback_used=True,
            )
        return AgentProposal(
            issue_id=issue_id,
            issue_type=issue_type,
            proposed_policy="repair",
            confidence=0.95,
            rationale=f"The hierarchy parity delta is exactly one missing HAS_FIELD edge and should be repaired or explicitly excluded after edge-level diff. {grounding}",
            missing_evidence=["exact missing HAS_FIELD src_node_id/tgt_node_id"],
            human_question="Can we generate the exact missing HAS_FIELD edge from v0 and v2 edge-level data?",
            fallback_used=True,
        )
    if relationship_type == "IMPLEMENTS":
        return AgentProposal(
            issue_id=issue_id,
            issue_type=issue_type,
            proposed_policy="needs_human",
            confidence=0.85,
            rationale=f"The semantic IMPLEMENTS delta needs edge-level classification before acceptance or repair. {grounding}",
            missing_evidence=["155 missing IMPLEMENTS edge list", "classification by repair/exclude/baseline-only"],
            human_question="Should we run an edge-level IMPLEMENTS diff before making a publish decision?",
            fallback_used=True,
        )

    if issue_type == "placeholder_path_missing_parent_metadata":
        if memory_policy == "quarantine" and memory_confidence >= 0.65:
            return AgentProposal(
                issue_id=issue_id,
                issue_type=issue_type,
                proposed_policy="quarantine",
                confidence=0.84,
                rationale=f"Similar approved decisions for placeholder-path orphans were quarantined, so this bounded source-quality exception should follow that pattern unless new evidence contradicts it. {grounding}",
                missing_evidence=["optional source-owner confirmation for placeholder/null path convention"],
                human_question="Do you accept quarantine for this placeholder-path orphan following the previous approved pattern?",
                fallback_used=True,
            )
        return AgentProposal(
            issue_id=issue_id,
            issue_type=issue_type,
            proposed_policy="needs_human",
            confidence=0.8,
            rationale=f"Placeholder null paths indicate incomplete source metadata; quarantine is plausible but requires explicit acceptance. {grounding}",
            missing_evidence=["raw source row showing why path and parent metadata are null"],
            human_question="Are these placeholder-path lineage endpoints acceptable as quarantined source-quality exceptions?",
            fallback_used=True,
        )

    if issue_type == "pathful_leaf_missing_parent_metadata":
        return AgentProposal(
            issue_id=issue_id,
            issue_type=issue_type,
            proposed_policy="quarantine",
            confidence=0.82,
            rationale=f"The node is a bounded rootless lineage endpoint with path evidence and no hierarchy children, so quarantine is safer than blocking forever. {grounding}",
            missing_evidence=["human acceptance of rootless lineage endpoint quarantine"],
            human_question="Do you approve quarantine for this rootless lineage endpoint?",
            fallback_used=True,
        )

    if issue_type == "duplicate_role_path_conflict":
        paths = evidence.get("paths") or []
        parent_ids = evidence.get("parent_node_ids") or []
        pattern = classify_path_pattern(paths)
        if memory_policy in {"accept", "quarantine"} and memory_confidence >= 0.65:
            return AgentProposal(
                issue_id=issue_id,
                issue_type=issue_type,
                proposed_policy=memory_policy,
                confidence=0.82,
                rationale=f"Similar approved duplicate-role/path decisions mostly used `{memory_policy}`, so the agent follows that governance memory while preserving the retrieved context. {grounding}",
                missing_evidence=[] if memory_policy == "accept" else ["domain confirmation if this conflict should move from quarantine to trusted"],
                human_question=f"Do you confirm `{memory_policy}` for this duplicate-role/path case based on the previous approved pattern?",
                fallback_used=True,
            )
        if len(parent_ids) <= 1 and pattern == "same_leaf_ontology_folder_variant":
            return AgentProposal(
                issue_id=issue_id,
                issue_type=issue_type,
                proposed_policy="accept",
                confidence=0.86,
                rationale=f"The duplicate roles share stable parent identity and differ by ontology folder naming while preserving the same leaf. {grounding}",
                missing_evidence=[],
                human_question="Do you accept this as an ontology-folder alias/move?",
                fallback_used=True,
            )
        return AgentProposal(
            issue_id=issue_id,
            issue_type=issue_type,
            proposed_policy="needs_human",
            confidence=0.68,
            rationale=f"The duplicate role/path conflict is not a strong ontology-folder alias pattern and needs manual classification. {grounding}",
            missing_evidence=["manual role/path acceptance or repair decision"],
            human_question="Should this node be accepted as an alias/move, repaired, or excluded?",
            fallback_used=True,
        )

    return AgentProposal(
        issue_id=issue_id,
        issue_type=issue_type,
        proposed_policy="needs_human",
        confidence=0.5,
        rationale=f"No deterministic policy matched this queue issue. {grounding}",
        missing_evidence=["manual review"],
        human_question="What publish policy should be assigned to this issue?",
        fallback_used=True,
    )


def grounding_phrase(
    counts: dict[str, Any],
    *,
    evidence: dict[str, Any] | None = None,
    issue_type: str | None = None,
    relationship_type: str | None = None,
) -> str:
    evidence = evidence or {}
    anchors = []
    if issue_type:
        anchors.append(f"issue_type `{issue_type}`")
    if relationship_type:
        anchors.append(f"relationship_type `{relationship_type}`")
    for key, label in (
        ("conflict_fields", "conflict field"),
        ("observed_roles", "observed role"),
        ("source_tables", "source table"),
    ):
        values = [str(value) for value in (evidence.get(key) or [])[:3] if value]
        if values:
            anchors.append(f"{label}(s) {', '.join(values)}")
    anchor_sentence = "Evidence anchors: " + "; ".join(anchors) + ". " if anchors else ""
    if not counts:
        return anchor_sentence + "No extra retrieval context was available."
    parts = [
        f"{int(counts.get('object_rows') or 0)} object row(s)",
        f"{int(counts.get('relationship_neighbors') or 0)} relationship neighbor(s)",
        f"{int(counts.get('lineage_examples') or 0)} lineage example(s)",
        f"{int(counts.get('similar_decisions') or 0)} similar approved decision(s)",
        f"{int(counts.get('schema_columns') or 0)} schema column match(es)",
        f"{int(counts.get('provenance_events') or 0)} provenance event(s)",
    ]
    return anchor_sentence + "Retrieved context includes " + ", ".join(parts) + "."


def policy_from_similar_decisions(decisions: list[dict[str, Any]]) -> tuple[str | None, float]:
    counts: dict[str, int] = {}
    for decision in decisions:
        policy = str(decision.get("publish_policy") or "").lower()
        status = str(decision.get("queue_status") or "").lower()
        if status not in {"approved", "resolved"} or policy not in {"accept", "quarantine", "exclude", "repair", "needs_human", "block"}:
            continue
        counts[policy] = counts.get(policy, 0) + 1
    total = sum(counts.values())
    if not total:
        return None, 0.0
    policy, count = max(counts.items(), key=lambda item: item[1])
    return policy, count / total


def classify_path_pattern(paths: list[Any]) -> str:
    path_values = [str(path) for path in paths if path]
    if not path_values:
        return "no_path"
    if len(path_values) == 1:
        return "single_path"
    normalized = {normalize_text(path) for path in path_values}
    if len(normalized) == 1:
        return "formatting_only"
    leaves = {normalize_text(path_leaf(path)) for path in path_values}
    joined = " ".join(path_values).lower()
    if len(leaves) == 1 and ("ontologie " in joined or "ontologies " in joined):
        return "same_leaf_ontology_folder_variant"
    if len(leaves) == 1:
        return "same_leaf_different_parent_path"
    return "different_path_or_label"


def path_leaf(path: str) -> str:
    parts = [part.strip() for part in path.replace("/", "\\").split("\\") if part.strip()]
    return parts[-1] if parts else ""


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())
