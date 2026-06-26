from __future__ import annotations

from typing import Any

from app.migration_v2.agents.base import AgentEvidencePlan, AgentQueryIntent


def build_evidence_plan(item: dict[str, Any]) -> AgentEvidencePlan:
    issue_id = str(item.get("issue_id") or "")
    issue_type = str(item.get("issue_type") or "")
    relationship_type = str(item.get("relationship_type") or "")
    evidence = item.get("evidence") or {}
    node_id = item.get("node_id")
    src_node_id = item.get("src_node_id") or evidence.get("src_node_id")
    tgt_node_id = item.get("tgt_node_id") or evidence.get("tgt_node_id")

    plan = AgentEvidencePlan(
        issue_id=issue_id,
        issue_type=issue_type,
        objective="Collect enough bounded evidence to propose a publish policy without mutating trusted graph data.",
        required_evidence=[
            "current validation queue record",
            "source staging rows for affected nodes",
            "relationship neighborhood for affected endpoints",
            "similar approved governance decisions",
            "provenance events for the affected object or issue",
        ],
        planned_tools=["retrieve_validation_evidence_packet"],
        repair_strategy="No repair can be applied by the agent; repair is proposed only when deterministic edge or parent evidence exists.",
        risk_flags=[],
    )

    if node_id:
        plan.planned_queries.extend(
            [
                AgentQueryIntent(
                    query_id="object_identity_rows",
                    purpose="Verify whether the affected node has conflicting names, paths, roles, parents, or source tables.",
                    sql=(
                        "SELECT node_id, parent_node_id, object_type, name_label, name_tech, path_full, "
                        "entity_type, data_type, status, source_table, publication_state::text "
                        "FROM catalog_object_staging "
                        "WHERE export_id = :export_id AND node_id = :node_id "
                        "ORDER BY object_type, source_table LIMIT 50"
                    ),
                    parameters={"export_id": item.get("export_id"), "node_id": node_id},
                ),
                AgentQueryIntent(
                    query_id="node_relationship_neighbors",
                    purpose="Check whether the affected node is isolated, parent-like, lineage-only, or linked to trusted endpoints.",
                    sql=(
                        "SELECT src_node_id, tgt_node_id, relationship_type, relationship_family, source_table, "
                        "publication_state::text, publication_reason "
                        "FROM catalog_relationship_staging "
                        "WHERE export_id = :export_id AND (src_node_id = :node_id OR tgt_node_id = :node_id) "
                        "ORDER BY relationship_type, source_table LIMIT 100"
                    ),
                    parameters={"export_id": item.get("export_id"), "node_id": node_id},
                ),
            ]
        )

    if issue_type == "duplicate_role_path_conflict":
        plan.objective = "Determine whether a duplicate node_id is a valid multi-role catalog object, an alias/move, or a conflation that must be quarantined or split."
        plan.required_evidence.extend(
            [
                "all staging rows sharing the duplicated node_id",
                "conflicting fields: parent_node_id, path_full, name_label, name_tech",
                "role source-of-truth or stable catalog-level role",
                "prior accepted decisions with the same conflict pattern",
            ]
        )
        plan.repair_strategy = (
            "If evidence proves a true alias/move, accept catalog-level role and retain observed roles as metadata. "
            "If labels or technical names identify distinct business objects, propose quarantine or a future deterministic split."
        )
        if evidence.get("conflict_fields"):
            plan.risk_flags.append("identity_conflict")

    elif issue_type in {"placeholder_path_missing_parent_metadata", "pathful_leaf_missing_parent_metadata"}:
        plan.objective = "Decide whether a rootless Field/UsageField is a bounded lineage endpoint, a source-quality placeholder, or a missing hierarchy repair."
        plan.required_evidence.extend(
            [
                "child count and parent-like evidence",
                "incoming and outgoing relationship context",
                "lineage path examples",
                "raw path and parent metadata from staging",
            ]
        )
        plan.repair_strategy = (
            "Do not invent a parent. Quarantine bounded lineage endpoints; propose repair only if exact parent evidence is retrieved."
        )
        plan.risk_flags.append("missing_parent_metadata")

    elif relationship_type == "HAS_FIELD":
        plan.objective = "Prove whether HAS_FIELD parity is a real missing structural edge or a legacy baseline counting defect."
        plan.required_evidence.extend(
            [
                "edge-level HAS_FIELD diff",
                "legacy blank-row count",
                "exact source and target identifiers if a real edge is missing",
            ]
        )
        plan.planned_queries.append(
            AgentQueryIntent(
                query_id="has_field_edge_candidates",
                purpose="Look for real HAS_FIELD endpoints before allowing repair.",
                sql=(
                    "SELECT src_node_id, tgt_node_id, relationship_type, source_table, publication_state::text "
                    "FROM catalog_relationship_staging "
                    "WHERE export_id = :export_id AND relationship_type = 'HAS_FIELD' "
                    "AND (:src_node_id IS NULL OR src_node_id = :src_node_id) "
                    "AND (:tgt_node_id IS NULL OR tgt_node_id = :tgt_node_id) "
                    "LIMIT 100"
                ),
                parameters={"export_id": item.get("export_id"), "src_node_id": src_node_id, "tgt_node_id": tgt_node_id},
            )
        )
        plan.repair_strategy = "Keep as repair unless edge-level comparison proves zero real missing edges or provides exact endpoints."
        plan.risk_flags.append("structural_edge_blocker")

    elif relationship_type == "IMPLEMENTS":
        plan.objective = "Classify each missing IMPLEMENTS semantic link as repaired, policy-excluded, or a v0/v2 baseline difference."
        plan.required_evidence.extend(
            [
                "edge-level IMPLEMENTS missing list",
                "inverse relationship evidence",
                "raw link type mapping from lien_link_entt",
                "business meaning of the semantic link",
            ]
        )
        plan.repair_strategy = (
            "Keep as needs_human until edge-level diff exists; repair only links with exact source and target identifiers."
        )
        plan.risk_flags.append("semantic_parity_delta")

    else:
        plan.required_evidence.append("human policy classification for unrecognized issue pattern")
        plan.risk_flags.append("unknown_issue_pattern")

    plan.planned_queries.append(
        AgentQueryIntent(
            query_id="similar_governance_decisions",
            purpose="Reuse governance memory only when previous human-approved cases match the current issue pattern.",
            sql=(
                "SELECT issue_id, issue_type, relationship_type, severity, publish_policy, queue_status, "
                "confidence, rationale, evidence "
                "FROM migration_validation_queue "
                "WHERE export_id = :export_id AND issue_id <> :issue_id "
                "AND queue_status IN ('approved', 'resolved') "
                "AND (issue_type = :issue_type OR relationship_type = :relationship_type) "
                "ORDER BY updated_at DESC LIMIT 25"
            ),
            parameters={
                "export_id": item.get("export_id"),
                "issue_id": issue_id,
                "issue_type": issue_type or None,
                "relationship_type": relationship_type or None,
            },
        )
    )
    return plan


def evidence_plan_to_dict(plan: AgentEvidencePlan) -> dict[str, Any]:
    return {
        "issue_id": plan.issue_id,
        "issue_type": plan.issue_type,
        "objective": plan.objective,
        "required_evidence": plan.required_evidence,
        "planned_queries": [
            {
                "query_id": query.query_id,
                "purpose": query.purpose,
                "sql": query.sql,
                "parameters": query.parameters,
                "safety": query.safety,
            }
            for query in plan.planned_queries
        ],
        "planned_tools": plan.planned_tools,
        "repair_strategy": plan.repair_strategy,
        "risk_flags": plan.risk_flags,
    }
