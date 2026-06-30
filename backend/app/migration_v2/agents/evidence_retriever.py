from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


def normalize_evidence(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
        except json.JSONDecodeError:
            return {"raw": value}
    return {}


def _to_dicts(rows: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _has_table(conn, table_name: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{table_name}"}).scalar())


def _like_token(value: str | None) -> str:
    value = str(value or "").strip()
    if not value:
        return "%"
    leaf = re.split(r"[\\/]", value)[-1]
    token = re.sub(r"[^A-Za-z0-9_]+", "%", leaf or value)
    return f"%{token[:80]}%"


def retrieve_evidence_packet(engine: Engine, export_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """Collect compact, read-only evidence that helps the agent ground a proposal."""

    evidence = normalize_evidence(item.get("evidence"))
    node_ids = {
        str(value)
        for value in [
            item.get("node_id"),
            item.get("src_node_id"),
            item.get("tgt_node_id"),
            *(evidence.get("parent_node_ids") or []),
        ]
        if value
    }
    labels = [str(value) for value in (evidence.get("labels") or []) if value]
    technical_names = [str(value) for value in (evidence.get("technical_names") or []) if value]
    paths = [str(value) for value in (evidence.get("paths") or []) if value]
    lookup_terms = [*labels[:4], *technical_names[:4], *[re.split(r"[\\/]", path)[-1] for path in paths[:4]]]

    packet: dict[str, Any] = {
        "retrieval_version": "validation-evidence-v1",
        "issue_id": item.get("issue_id"),
        "node_ids": sorted(node_ids),
        "lookup_terms": lookup_terms[:8],
        "object_rows": [],
        "relationship_neighbors": [],
        "lineage_examples": [],
        "similar_decisions": [],
        "schema_columns": [],
        "provenance_events": [],
    }

    with engine.connect() as conn:
        if node_ids and _has_table(conn, "catalog_object_staging"):
            packet["object_rows"] = _to_dicts(conn.execute(text("""
                SELECT node_id, parent_node_id, object_type, name_label, name_tech, path_full,
                       entity_type, data_type, status, app_code, source_table,
                       publication_state::text AS publication_state,
                       publication_reason
                FROM catalog_object_staging
                WHERE export_id = :export_id AND node_id = ANY(:node_ids)
                ORDER BY object_type, source_table
                LIMIT 24
            """), {"export_id": export_id, "node_ids": list(node_ids)}).mappings().all())

        if node_ids and _has_table(conn, "catalog_relationship_staging"):
            packet["relationship_neighbors"] = _to_dicts(conn.execute(text("""
                SELECT src_node_id, tgt_node_id, relationship_type, relationship_family,
                       source_table, link_type, status,
                       publication_state::text AS publication_state,
                       publication_reason
                FROM catalog_relationship_staging
                WHERE export_id = :export_id
                  AND (src_node_id = ANY(:node_ids) OR tgt_node_id = ANY(:node_ids))
                ORDER BY relationship_type, source_table
                LIMIT 40
            """), {"export_id": export_id, "node_ids": list(node_ids)}).mappings().all())

        if node_ids and _has_table(conn, "lineage_path"):
            packet["lineage_examples"] = _to_dicts(conn.execute(text("""
                SELECT start_node_id, end_node_id, path_family, path_length,
                       path_nodes, path_relationships, evidence
                FROM lineage_path
                WHERE export_id = :export_id
                  AND (start_node_id = ANY(:node_ids) OR end_node_id = ANY(:node_ids)
                       OR path_nodes ?| :node_ids)
                ORDER BY path_length, path_family
                LIMIT 12
            """), {"export_id": export_id, "node_ids": list(node_ids)}).mappings().all())

        if _has_table(conn, "migration_validation_queue"):
            packet["similar_decisions"] = _to_dicts(conn.execute(text("""
                SELECT issue_id, issue_type, entity_kind, relationship_type, severity,
                       publish_policy, queue_status, confidence, rationale,
                       evidence -> 'conflict_fields' AS conflict_fields,
                       evidence -> 'observed_roles' AS observed_roles,
                       evidence -> 'source_tables' AS source_tables
                FROM migration_validation_queue
                WHERE export_id = :export_id
                  AND issue_id <> :issue_id
                  AND queue_status IN ('approved', 'resolved')
                  AND (
                    issue_type = :issue_type
                    OR relationship_type = :relationship_type
                    OR node_id = ANY(:node_ids)
                  )
                ORDER BY updated_at DESC
                LIMIT 12
            """), {
                "export_id": export_id,
                "issue_id": item.get("issue_id"),
                "issue_type": item.get("issue_type"),
                "relationship_type": item.get("relationship_type"),
                "node_ids": list(node_ids) or [""],
            }).mappings().all())

        if lookup_terms and _has_table(conn, "migration_column_profile"):
            schema_rows: list[dict[str, Any]] = []
            for term in lookup_terms[:5]:
                rows = conn.execute(text("""
                    SELECT raw_table_name, column_name, data_type_guess, null_count,
                           distinct_count, non_null_count, sample_values, warnings
                    FROM migration_column_profile
                    WHERE export_id = :export_id
                      AND (column_name ILIKE :term OR sample_values::text ILIKE :term)
                    ORDER BY raw_table_name, column_name
                    LIMIT 8
                """), {"export_id": export_id, "term": _like_token(term)}).mappings().all()
                schema_rows.extend(_to_dicts(rows))
            deduped = {}
            for row in schema_rows:
                deduped[(row["raw_table_name"], row["column_name"])] = row
            packet["schema_columns"] = list(deduped.values())[:20]

        if _has_table(conn, "migration_governance_provenance"):
            subjects = list(node_ids) + [str(item.get("issue_id") or "")]
            events: list[dict[str, Any]] = []
            for subject in [value for value in subjects if value][:6]:
                rows = conn.execute(text("""
                    SELECT event_id, event_type, actor, status, occurred_at, subject_id, payload
                    FROM migration_governance_provenance
                    WHERE export_id = :export_id
                      AND (subject_id = :subject OR payload::text ILIKE '%' || :subject || '%')
                    ORDER BY occurred_at DESC
                    LIMIT 5
                """), {"export_id": export_id, "subject": subject}).mappings().all()
                events.extend(_to_dicts(rows))
            seen = set()
            compact_events = []
            for event in events:
                key = event.get("event_id")
                if key in seen:
                    continue
                seen.add(key)
                compact_events.append(event)
            packet["provenance_events"] = compact_events[:15]

    packet["counts"] = {
        "object_rows": len(packet["object_rows"]),
        "relationship_neighbors": len(packet["relationship_neighbors"]),
        "lineage_examples": len(packet["lineage_examples"]),
        "similar_decisions": len(packet["similar_decisions"]),
        "schema_columns": len(packet["schema_columns"]),
        "provenance_events": len(packet["provenance_events"]),
    }
    return packet
