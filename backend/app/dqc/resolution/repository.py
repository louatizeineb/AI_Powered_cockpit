from __future__ import annotations
import json
import math
from typing import Any
from sqlalchemy import text
from app.db import SessionLocal


def reset_workspace() -> dict:
    tables = [
        "dqc_match_candidate",
        "dqc_resolved",
        "dqc_dlq",
        "dqc_normalized",
        "dqc_raw",
        "pipeline_logs",
    ]
    with SessionLocal() as db:
        db.execute(text(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        db.commit()
    return {"status": "reset", "tables": tables}


def _clean_json_value(value: Any) -> Any:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {k: _clean_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_json_value(v) for v in value]
    if isinstance(value, tuple):
        return [_clean_json_value(v) for v in value]
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_clean_json_value(value), allow_nan=False)


def save_raw(event: dict, run_id: str, source_system: str) -> int:
    event = _clean_json_value(event)
    with SessionLocal() as db:
        row = db.execute(text("""
            INSERT INTO dqc_raw(run_id, source_system, raw_payload)
            VALUES (:run_id, :source_system, CAST(:raw_payload AS JSONB))
            RETURNING id
        """), {"run_id": run_id, "source_system": source_system, "raw_payload": _json_dumps(event)}).mappings().one()
        db.commit()
        return int(row["id"])


def save_normalized(raw_id: int, normalized: dict) -> int:
    normalized = _clean_json_value(normalized)
    with SessionLocal() as db:
        row = db.execute(text("""
            INSERT INTO dqc_normalized(
                raw_id, raw_dqc_id, source_system, application_code_raw, application_code_norm,
                controlled_object_name_raw, controlled_source_name_raw,
                controlled_structure_name, controlled_field_name, target_level,
                quality_dimension, control_name, control_tool, cdq_profile, control_link,
                acceptance_threshold, controlled_item_count, ok_count, ko_count, ko_rate, quality_score,
                normalized_payload
            ) VALUES (
                :raw_id, :raw_dqc_id, :source_system, :application_code_raw, :application_code_norm,
                :controlled_object_name_raw, :controlled_source_name_raw,
                :controlled_structure_name, :controlled_field_name, :target_level,
                :quality_dimension, :control_name, :control_tool, :cdq_profile, :control_link,
                :acceptance_threshold, :controlled_item_count, :ok_count, :ko_count, :ko_rate, :quality_score,
                CAST(:normalized_payload AS JSONB)
            ) RETURNING id
        """), {**normalized, "raw_id": raw_id, "normalized_payload": _json_dumps(normalized)}).mappings().one()
        db.commit()
        return int(row["id"])


def save_dlq(run_id: str, raw_id: int | None, normalized_id: int | None, stage: str, reason: str, details: dict, llm_analysis: str | None = None) -> int:
    details = _clean_json_value(details)
    with SessionLocal() as db:
        row = db.execute(text("""
            INSERT INTO dqc_dlq(run_id, raw_id, normalized_id, failure_stage, failure_reason, failure_details, llm_analysis)
            VALUES (:run_id, :raw_id, :normalized_id, :stage, :reason, CAST(:details AS JSONB), :llm_analysis)
            RETURNING id
        """), {"run_id": run_id, "raw_id": raw_id, "normalized_id": normalized_id, "stage": stage, "reason": reason, "details": _json_dumps(details), "llm_analysis": llm_analysis}).mappings().one()
        db.commit()
        return int(row["id"])


def find_path_candidates(normalized: dict, limit: int = 50) -> list[dict]:
    app = normalized.get("application_code_norm")
    target = normalized.get("target_level")
    field = normalized.get("controlled_field_name")
    structure = normalized.get("controlled_structure_name")
    source = normalized.get("controlled_source_name_norm")

    params = {"limit": limit, "app": app, "target": target, "field": field, "structure": structure, "source": source}
    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT id, entity_table, entity_level, node_id, raw_path_full, normalized_path,
                   app_code_from_path, leaf_name, parent_name, path_depth, path_segments, path_tokens
            FROM catalog_path_index
            WHERE (:app IS NULL OR app_code_from_path = :app)
              AND (:target IS NULL OR entity_level = :target OR (:target = 'Field' AND entity_level IN ('Field','Structure')))
              AND (
                    (:field IS NOT NULL AND (leaf_name = :field OR :field = ANY(path_tokens)))
                 OR (:structure IS NOT NULL AND (leaf_name = :structure OR parent_name = :structure OR :structure = ANY(path_tokens) OR normalized_path LIKE '%' || :structure || '%'))
                 OR (:source IS NOT NULL AND (:source = ANY(path_tokens) OR normalized_path LIKE '%' || :source || '%'))
              )
            LIMIT :limit
        """), params).mappings().all()
    return [dict(r) for r in rows]


def save_candidates(normalized_id: int, candidates: list[dict]) -> None:
    with SessionLocal() as db:
        for rank, c in enumerate(candidates, start=1):
            db.execute(text("""
                INSERT INTO dqc_match_candidate(
                    normalized_id, candidate_node_id, candidate_entity_level, candidate_path_full,
                    match_method, match_score, match_reasons, rank
                ) VALUES (
                    :normalized_id, :node_id, :entity_level, :raw_path_full,
                    :match_method, :match_score, CAST(:match_reasons AS JSONB), :rank
                )
            """), {**c, "normalized_id": normalized_id, "match_reasons": _json_dumps(c.get("match_reasons", [])), "rank": rank})
        db.commit()


def save_resolved(normalized_id: int, candidate: dict, confidence_level: str, human_review_required: bool) -> int:
    with SessionLocal() as db:
        row = db.execute(text("""
            INSERT INTO dqc_resolved(
                normalized_id, matched_node_id, matched_entity_level, matched_path_full,
                match_method, match_score, confidence_level, human_review_required, resolution_status
            ) VALUES (
                :normalized_id, :node_id, :entity_level, :raw_path_full,
                :match_method, :match_score, :confidence_level, :human_review_required, :resolution_status
            ) RETURNING id
        """), {**candidate, "normalized_id": normalized_id, "confidence_level": confidence_level, "human_review_required": human_review_required, "resolution_status": "MATCHED_WITH_REVIEW" if human_review_required else "MATCHED"}).mappings().one()
        db.commit()
        return int(row["id"])


def _with_control_status(row: dict) -> dict:
    controlled = row.get("controlled_item_count")
    ok = row.get("ok_count")
    threshold = row.get("acceptance_threshold")

    ratio = None
    score = None
    status = "UNKNOWN"

    if controlled:
        ratio = ok / controlled if ok is not None else None
        score = round(ratio * 100, 2) if ratio is not None else None

    if score is not None and threshold is not None:
        threshold_score = threshold * 100 if threshold <= 1 else threshold
        status = "PASSED" if score >= threshold_score else "FAILED"
    elif score is not None:
        status = "NO_THRESHOLD"

    return {
        **row,
        "control_ratio": round(ratio, 6) if ratio is not None else None,
        "control_score": score,
        "control_status": status,
    }


def list_resolved(limit: int = 100) -> list[dict]:
    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT r.*,
                   n.application_code_norm,
                   n.controlled_structure_name,
                   n.controlled_field_name,
                   n.quality_dimension,
                   n.control_name,
                   n.control_tool,
                   n.acceptance_threshold,
                   n.controlled_item_count,
                   n.ok_count,
                   n.ko_count,
                   n.ko_rate,
                   n.quality_score
            FROM dqc_resolved r
            JOIN dqc_normalized n ON n.id = r.normalized_id
            ORDER BY r.id DESC LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return [_with_control_status(dict(r)) for r in rows]


def list_dlq(limit: int = 100) -> list[dict]:
    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT * FROM dqc_dlq ORDER BY id DESC LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return [dict(r) for r in rows]


def approve_match(resolved_id: int, reviewer: str, note: str | None = None) -> dict:
    with SessionLocal() as db:
        db.execute(text("""
            UPDATE dqc_resolved
            SET reviewed = true, reviewed_by = :reviewer, reviewed_at = now(), review_note = :note,
                human_review_required = false, resolution_status = 'MATCHED_APPROVED'
            WHERE id = :resolved_id
        """), {"resolved_id": resolved_id, "reviewer": reviewer, "note": note})
        db.commit()
    return {"status": "approved", "resolved_id": resolved_id}


def reject_match(resolved_id: int, reviewer: str, reason: str) -> dict:
    with SessionLocal() as db:
        db.execute(text("""
            UPDATE dqc_resolved
            SET reviewed = true, reviewed_by = :reviewer, reviewed_at = now(), review_note = :reason,
                resolution_status = 'MATCH_REJECTED'
            WHERE id = :resolved_id
        """), {"resolved_id": resolved_id, "reviewer": reviewer, "reason": reason})
        db.commit()
    return {"status": "rejected", "resolved_id": resolved_id}
