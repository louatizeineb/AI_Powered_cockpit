from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.orm import Session


def _reference_key(*parts: str | None) -> str:
    clean = "|".join(str(p or "").strip().lower() for p in parts)
    return hashlib.sha1(clean.encode("utf-8")).hexdigest()


def _resolution(status: str, label: str | None, app_code: str | None, object_name: str | None, method: str | None, rows=None) -> dict:
    rows = list(rows or [])
    if status == "MATCHED" and len(rows) == 1:
        row = rows[0]
        return {
            "matched_node_id": row["node_id"],
            "matched_label": row["label"],
            "catalog_reference_key": _reference_key(label, app_code, object_name, row["node_id"]),
            "match_method": method,
            "confidence": 0.95,
            "status": "MATCHED",
        }
    return {
        "matched_node_id": None,
        "matched_label": label,
        "catalog_reference_key": _reference_key(label, app_code, object_name),
        "match_method": method,
        "confidence": 0.0,
        "status": status,
    }


def _classify(rows, label: str, app_code: str, object_name: str, method: str) -> dict:
    rows = list(rows)
    if len(rows) == 0:
        return _resolution("UNRESOLVED_NOT_FOUND", label, app_code, object_name, method)
    if len(rows) > 1:
        return _resolution("UNRESOLVED_AMBIGUOUS", label, app_code, object_name, method)
    return _resolution("MATCHED", label, app_code, object_name, method, rows)


def resolve_dataquality_event(db: Session, dq_result: dict) -> dict:
    app_code = (dq_result.get("application_code") or "").strip()
    object_name = (dq_result.get("controlled_object_name") or "").strip()
    object_type = (dq_result.get("controlled_object_type") or "").strip().upper()

    if not app_code or not object_name:
        return _resolution(
            "UNRESOLVED_MISSING_REQUIRED_RESOLUTION_INPUT",
            None,
            app_code,
            object_name,
            None,
        )

    # DQC field resolution: app_code is a scope, never the final match by itself.
    if object_type in {"FIELD", "COLUMN", "COLONNE"}:
        rows = db.execute(
            text(
                """
                SELECT f.node_id, 'Field' AS label
                FROM field f
                WHERE lower(coalesce(f.app_code, '')) = lower(:app_code)
                  AND (
                        lower(coalesce(f.name_label, '')) = lower(:name)
                     OR lower(coalesce(f.name_tech, '')) = lower(:name)
                     OR lower(coalesce(f.path_full, '')) LIKE lower('%' || :name)
                  )
                LIMIT 5
                """
            ),
            {"app_code": app_code, "name": object_name},
        ).mappings().all()
        return _classify(rows, "Field", app_code, object_name, "app_code + controlledObjectName -> field")

    # Structure resolution: narrow candidates by a Source with the same app_code in the path hierarchy when possible.
    if object_type in {"TABLE", "STRUCTURE", "TOPIC", "VIEW", "VUE"}:
        rows = db.execute(
            text(
                """
                WITH source_scope AS (
                    SELECT node_id, app_code
                    FROM source
                    WHERE lower(app_code) = lower(:app_code)
                )
                SELECT DISTINCT st.node_id, 'Structure' AS label
                FROM structure st
                LEFT JOIN container c ON c.node_id = st.parent_node_id
                LEFT JOIN source s_direct ON s_direct.node_id = st.parent_node_id
                LEFT JOIN source s_container ON s_container.node_id = c.parent_node_id
                WHERE (
                        lower(coalesce(st.name_label, '')) = lower(:name)
                     OR lower(coalesce(st.name_tech, '')) = lower(:name)
                     OR lower(coalesce(st.path_full, '')) LIKE lower('%' || :name)
                )
                AND (
                    lower(coalesce(s_direct.app_code, '')) = lower(:app_code)
                    OR lower(coalesce(s_container.app_code, '')) = lower(:app_code)
                    OR EXISTS (SELECT 1 FROM source_scope)
                )
                LIMIT 5
                """
            ),
            {"app_code": app_code, "name": object_name},
        ).mappings().all()
        return _classify(rows, "Structure", app_code, object_name, "app_code + controlledObjectName -> structure")

    return _resolution("UNRESOLVED_UNSUPPORTED_OBJECT_TYPE", None, app_code, object_name, None)
