from __future__ import annotations

# This module contains resolver functions that attempt to match incoming event data to existing catalog nodes in the Lineage Cockpit database.
# The resolvers use simple heuristics based on key attributes of the events to find potential matches in the 'structure', 'source', and 'link' tables. Each resolver returns a standardized dictionary indicating the match results, including matched node ID, label, reference key, match method, confidence score


import hashlib

from sqlalchemy import text
from sqlalchemy.orm import Session


def _reference_key(*parts: str | None) -> str:
    clean = "|".join(str(p or "").strip().lower() for p in parts)
    return hashlib.sha1(clean.encode("utf-8")).hexdigest()


def _no_match(*parts: str | None) -> dict:
    return {
        "matched_node_id": None,
        "matched_label": None,
        "catalog_reference_key": _reference_key(*parts),
        "match_method": None,
        "confidence": 0.0,
        "status": "UNRESOLVED",
    }


def resolve_dataquality_event(db: Session, dq_result: dict) -> dict:
    app_code = dq_result.get("application_code")
    object_name = dq_result.get("controlled_object_name")
    business_term_name = dq_result.get("business_term_name")

    if object_name:
        result = db.execute(
            text(
                """
                SELECT node_id, 'Structure' AS label
                FROM structure
                WHERE lower(name_label) = lower(:name)
                   OR lower(name_tech) = lower(:name)
                   OR path_full ILIKE '%' || :name || '%'
                LIMIT 1
                """
            ),
            {"name": object_name},
        ).mappings().first()
        if result:
            return {
                "matched_node_id": result["node_id"],
                "matched_label": result["label"],
                "catalog_reference_key": _reference_key("Structure", object_name, result["node_id"]),
                "match_method": "controlledObjectName -> structure.name_label/name_tech/path_full",
                "confidence": 0.90,
                "status": "MATCHED",
            }

    if app_code:
        result = db.execute(
            text(
                """
                SELECT node_id, 'Source' AS label
                FROM source
                WHERE app_code = :app_code
                LIMIT 1
                """
            ),
            {"app_code": app_code},
        ).mappings().first()
        if result:
            return {
                "matched_node_id": result["node_id"],
                "matched_label": result["label"],
                "catalog_reference_key": _reference_key("Source", app_code, result["node_id"]),
                "match_method": "applicationCode -> source.app_code",
                "confidence": 0.75,
                "status": "MATCHED",
            }

    if business_term_name:
        result = db.execute(
            text(
                """
                SELECT tgt_node_id AS node_id, 'BusinessTerm' AS label
                FROM link
                WHERE lower(tgt_name_label) = lower(:name)
                   OR lower(tgt_name_tech) = lower(:name)
                LIMIT 1
                """
            ),
            {"name": business_term_name},
        ).mappings().first()
        if result:
            return {
                "matched_node_id": result["node_id"],
                "matched_label": result["label"],
                "catalog_reference_key": _reference_key("BusinessTerm", business_term_name, result["node_id"]),
                "match_method": "businessTermName -> link.tgt_name_label/name_tech",
                "confidence": 0.70,
                "status": "MATCHED",
            }

    return _no_match("DataQuality", app_code, object_name, business_term_name)


def resolve_pipeline_event(db: Session, pipeline_run: dict) -> dict:
    source_table = pipeline_run.get("source_table")
    source_db = pipeline_run.get("source_database")

    if source_table:
        result = db.execute(
            text(
                """
                SELECT node_id, 'Structure' AS label
                FROM structure
                WHERE lower(name_label) = lower(:name)
                   OR lower(name_tech) = lower(:name)
                   OR path_full ILIKE '%' || :name || '%'
                LIMIT 1
                """
            ),
            {"name": source_table},
        ).mappings().first()
        if result:
            return {
                "matched_node_id": result["node_id"],
                "matched_label": result["label"],
                "catalog_reference_key": _reference_key("Structure", source_table, result["node_id"]),
                "match_method": "source.table -> structure.name_label/name_tech/path_full",
                "confidence": 0.85,
                "status": "MATCHED",
            }

    return _no_match("Pipeline", source_db, source_table, pipeline_run.get("pipeline_name"))
