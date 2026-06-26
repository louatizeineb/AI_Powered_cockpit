from __future__ import annotations

from collections import Counter
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


POLICY_VERSION = "conditional-publish-v1"
HARD_FINDING_CATEGORIES = {
    "duplicate_identity_conflict",
    "hierarchy_cycle",
    "missing_relationship_endpoint",
    "unresolved_parent",
}


def _counts(conn, table: str, export_id: str) -> dict[str, int]:
    rows = conn.execute(
        text(
            f"""SELECT effective_state, count(*) FROM (
                SELECT CASE
                    WHEN NOT is_graph_eligible THEN 'excluded'
                    WHEN publication_state = 'review_pending' THEN 'trusted'
                    ELSE publication_state::text
                END AS effective_state
                FROM {table} WHERE export_id = :export_id
            ) classified GROUP BY effective_state"""
        ),
        {"export_id": export_id},
    ).all()
    return {str(state): int(count) for state, count in rows}


def apply_conditional_policy(
    engine: Engine,
    export_id: str,
    *,
    policy_version: str = POLICY_VERSION,
    decided_by: str = "deterministic_policy_engine",
) -> dict[str, Any]:
    with engine.begin() as conn:
        # Reset only prior sparse decisions. Unclassified eligible rows are effectively trusted by the views.
        common = {
            "export_id": export_id,
            "policy_version": policy_version,
            "decided_by": decided_by,
        }
        for table in ("catalog_object_staging", "catalog_relationship_staging"):
            conn.execute(
                text(
                    f"""
                    UPDATE {table}
                    SET publication_state = 'review_pending',
                        publication_reason = NULL,
                        publication_policy_version = NULL,
                        publication_decided_by = NULL,
                        publication_decided_at = NULL,
                        publication_evidence = '{{}}'::jsonb
                    WHERE export_id = :export_id
                      AND publication_policy_version IS NOT NULL
                    """
                ),
                common,
            )

        queue_rows = conn.execute(
            text(
                """
                SELECT issue_id, issue_type, entity_kind, node_id, src_node_id, tgt_node_id,
                       relationship_type, severity, publish_policy, queue_status,
                       proposed_action, rationale, evidence
                FROM migration_validation_queue
                WHERE export_id = :export_id
                ORDER BY issue_id
                """
            ),
            {"export_id": export_id},
        ).mappings().all()

        aggregate_blockers: list[dict[str, Any]] = []
        aggregate_reviews: list[dict[str, Any]] = []
        policy_events: Counter[str] = Counter()
        for row in queue_rows:
            policy = str(row["publish_policy"] or row["proposed_action"] or "review_pending").lower()
            status = str(row["queue_status"] or "pending").lower()
            approved = status in {"approved", "resolved"}
            if approved and policy in {"accept", "trusted"}:
                state = "trusted"
            elif approved and policy == "exclude":
                state = "excluded"
            elif approved and policy == "quarantine":
                state = "quarantine"
            elif policy in {"repair"}:
                state = "repair"
            elif policy in {"block", "hard_block"} or str(row["severity"]).upper() == "ERROR":
                state = "hard_block"
            else:
                # Bounded unresolved evidence is preserved but removed from normal projections.
                state = "quarantine"

            evidence = {
                "queue_issue_id": row["issue_id"],
                "queue_status": status,
                "publish_policy": policy,
            }
            target_table = "catalog_relationship_staging" if row["entity_kind"] == "relationship" else "catalog_object_staging"
            predicates: list[str] = []
            params = {**common, "state": state, "reason": row["rationale"], "evidence": json.dumps(evidence)}
            if target_table == "catalog_object_staging" and row["node_id"]:
                predicates.append("node_id = :node_id")
                params["node_id"] = row["node_id"]
            elif target_table == "catalog_relationship_staging" and row["src_node_id"] and row["tgt_node_id"]:
                predicates.extend(("src_node_id = :src_node_id", "tgt_node_id = :tgt_node_id"))
                params.update(src_node_id=row["src_node_id"], tgt_node_id=row["tgt_node_id"])
                if row["relationship_type"]:
                    predicates.append("relationship_type = :relationship_type")
                    params["relationship_type"] = row["relationship_type"]
            else:
                # Aggregate differences cannot safely identify which edges to remove.
                if approved and state in {"trusted", "excluded"}:
                    policy_events[f"aggregate_{state}"] += 1
                    continue
                item = {
                    "issue_id": row["issue_id"],
                    "relationship_type": row["relationship_type"],
                    "reason": "aggregate_issue_has_no_edge_level_identity",
                }
                if state in {"repair", "hard_block"} or row["relationship_type"] == "HAS_FIELD":
                    aggregate_blockers.append({
                        **item,
                        "required_action": "produce_edge_level_diff_and_repair",
                    })
                else:
                    aggregate_reviews.append(item)
                continue

            conn.execute(
                text(
                    f"""
                    UPDATE {target_table}
                    SET publication_state = CAST(:state AS migration_publication_state),
                        publication_reason = coalesce(:reason, 'validation_queue_policy'),
                        publication_policy_version = :policy_version,
                        publication_decided_by = :decided_by,
                        publication_decided_at = now(),
                        publication_evidence = CAST(:evidence AS jsonb)
                    WHERE export_id = :export_id AND {' AND '.join(predicates)}
                    """
                ),
                params,
            )
            policy_events[state] += 1

        # Relationships cannot be trusted when either endpoint is outside the trusted slice.
        conn.execute(
            text(
                """
                UPDATE catalog_relationship_staging relationship
                SET publication_state = 'quarantine',
                    publication_reason = 'endpoint_not_in_trusted_projection',
                    publication_policy_version = :policy_version,
                    publication_decided_by = :decided_by,
                    publication_decided_at = now()
                WHERE relationship.export_id = :export_id
                  AND relationship.publication_state IN ('trusted', 'review_pending')
                  AND (
                      NOT EXISTS (
                          SELECT 1 FROM catalog_object_staging source
                          WHERE source.export_id = relationship.export_id
                            AND source.node_id = relationship.src_node_id
                            AND source.is_graph_eligible
                            AND source.publication_state IN ('trusted', 'review_pending')
                      ) OR NOT EXISTS (
                          SELECT 1 FROM catalog_object_staging target
                          WHERE target.export_id = relationship.export_id
                            AND target.node_id = relationship.tgt_node_id
                            AND target.is_graph_eligible
                            AND target.publication_state IN ('trusted', 'review_pending')
                      )
                  )
                """
            ),
            common,
        )

        findings = conn.execute(
            text(
                """
                SELECT id, severity, category, node_id, relationship_id, message
                FROM migration_validation_finding
                WHERE export_id = :export_id AND status = 'open' AND severity = 'ERROR'
                ORDER BY id
                """
            ),
            {"export_id": export_id},
        ).mappings().all()
        hard_blockers = aggregate_blockers + [
            {"finding_id": row["id"], "category": row["category"], "message": row["message"]}
            for row in findings
        ]
        object_counts = _counts(conn, "catalog_object_staging", export_id)
        relationship_counts = _counts(conn, "catalog_relationship_staging", export_id)
        status = "blocked" if hard_blockers or object_counts.get("hard_block", 0) or object_counts.get("repair", 0) \
            or relationship_counts.get("hard_block", 0) or relationship_counts.get("repair", 0) else "ready"
        snapshot_id = conn.execute(
            text(
                """
                INSERT INTO migration_publication_snapshot(
                    export_id, policy_version, status, object_counts, relationship_counts,
                    hard_blockers, evidence, created_by
                ) VALUES (
                    :export_id, :policy_version, :status, CAST(:object_counts AS jsonb),
                    CAST(:relationship_counts AS jsonb), CAST(:hard_blockers AS jsonb),
                    CAST(:evidence AS jsonb), :decided_by
                ) RETURNING id
                """
            ),
            {
                **common,
                "status": status,
                "object_counts": json.dumps(object_counts),
                "relationship_counts": json.dumps(relationship_counts),
                "hard_blockers": json.dumps(hard_blockers),
                "evidence": json.dumps({
                    "queue_items_evaluated": len(queue_rows),
                    "policy_events": dict(policy_events),
                    "aggregate_reviews": aggregate_reviews,
                }),
            },
        ).scalar_one()

    return {
        "export_id": export_id,
        "status": status,
        "policy_version": policy_version,
        "snapshot_id": snapshot_id,
        "object_counts": object_counts,
        "relationship_counts": relationship_counts,
        "hard_blockers": hard_blockers,
        "aggregate_reviews": aggregate_reviews,
        "review_pending_count": len(aggregate_reviews),
        "queue_items_evaluated": len(queue_rows),
    }
