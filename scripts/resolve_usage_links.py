# scripts/resolve_usage_links.py

from __future__ import annotations

import os
import re
from typing import Optional

from neo4j import GraphDatabase


NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "change_me")

BATCH_SIZE = 1000


def normalize_value(value: Optional[str]) -> str:
    if not value:
        return ""

    value = value.replace("\\", "/")
    value = value.replace(" > ", "/")
    value = re.sub(r"/+", "/", value)
    value = value.strip().strip("/")
    return value.lower()


def extract_app_code(path: Optional[str], known_app_codes: set[str]) -> Optional[str]:
    normalized = normalize_value(path)

    if not normalized:
        return None

    parts = [p.strip().upper() for p in normalized.split("/") if p.strip()]

    for part in parts:
        # handle "XRM > KAFKA" after normalization too
        first_token = part.split()[0].strip().upper()

        if part in known_app_codes:
            return part

        if first_token in known_app_codes:
            return first_token

    return None


def load_source_lookup(session) -> dict[str, str]:
    result = session.run(
        """
        MATCH (s:Source)
        RETURN 
          s.node_id AS node_id,
          s.app_code AS app_code,
          s.name_tech AS name_tech,
          s.name_label AS name_label
        """
    )

    lookup: dict[str, str] = {}

    for row in result:
        source_id = row["node_id"]

        for key in ["app_code", "name_tech", "name_label"]:
            value = row[key]
            norm = normalize_value(value)

            if norm and source_id:
                lookup[norm.upper()] = source_id

    return lookup


def fetch_usage_batch(session):
    result = session.run(
        """
        MATCH (u:Usage)
        WHERE NOT (u)-[:RESOLVED_TO_SOURCE]->(:Source)
          AND coalesce(u.source_resolution_status, "") <> "RESOLVED"
        RETURN 
          u.usage_uuid AS usage_uuid,
          u.usage_path AS usage_path,
          u.usage_name AS usage_name,
          u.usage_tech_name AS usage_tech_name,
          u.usage_type_path AS usage_type_path,
          u.path_full AS path_full,
          u.usage_kind AS usage_kind
        LIMIT $limit
        """,
        limit=BATCH_SIZE,
    )

    return list(result)


def write_resolved_batch(session, rows):
    if not rows:
        return 0

    result = session.run(
        """
        UNWIND $rows AS row

        MATCH (u:Usage {usage_uuid: row.usage_uuid})
        MATCH (s:Source {node_id: row.source_id})

        MERGE (u)-[r:RESOLVED_TO_SOURCE]->(s)
        SET 
          r.method = row.method,
          r.extracted_app_code = row.extracted_app_code,
          r.confidence = row.confidence,
          r.created_at = datetime(),
          u.source_resolution_status = "RESOLVED",
          u.source_resolution_attempted_at = datetime()

        RETURN count(r) AS created_count
        """,
        rows=rows,
    )

    return result.single()["created_count"]


def write_unresolved_batch(session, rows):
    if not rows:
        return 0

    result = session.run(
        """
        UNWIND $rows AS row

        MATCH (u:Usage {usage_uuid: row.usage_uuid})
        SET 
          u.source_resolution_status = "UNRESOLVED",
          u.source_resolution_reason = row.reason,
          u.source_resolution_attempted_at = datetime(),
          u.extracted_app_code = row.extracted_app_code

        RETURN count(u) AS unresolved_count
        """,
        rows=rows,
    )

    return result.single()["unresolved_count"]

def extract_app_code_from_usage_row(row, known_app_codes: set[str]) -> tuple[Optional[str], Optional[str]]:
    candidates = {
        "usage_path": row.get("usage_path"),
        "usage_name": row.get("usage_name"),
        "usage_tech_name": row.get("usage_tech_name"),
        "usage_type_path": row.get("usage_type_path"),
        "path_full": row.get("path_full"),
        "usage_kind": row.get("usage_kind"),
    }

    for field_name, value in candidates.items():
        app_code = extract_app_code(value, known_app_codes)
        if app_code:
            return app_code, field_name

    return None, None

def main():
    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
    )

    total_processed = 0
    total_resolved = 0
    total_unresolved = 0

    with driver.session() as session:
        source_lookup = load_source_lookup(session)
        known_app_codes = set(source_lookup.keys())
        print(f"Loaded source lookup keys: {len(source_lookup)}")

        while True:
            usages = fetch_usage_batch(session)

            if not usages:
                break

            resolved_rows = []
            unresolved_rows = []

            for row in usages:
                usage_uuid = row["usage_uuid"]
                usage_path = row["usage_path"]

                app_code, matched_field = extract_app_code_from_usage_row(row, known_app_codes)

                if not app_code:
                    unresolved_rows.append(
                        {
                            "usage_uuid": usage_uuid,
                            "extracted_app_code": None,
                            "reason": "NO_APP_CODE_EXTRACTED",
                        }
                    )
                    continue

                source_id = source_lookup.get(app_code)

                if source_id:
                    resolved_rows.append(
                        {
                            "usage_uuid": usage_uuid,
                            "source_id": source_id,
                            "extracted_app_code": app_code,
                            "method": f"usage_property_app_code:{matched_field}",
                            "confidence": 0.85,
                        }
                    )
                else:
                    unresolved_rows.append(
                        {
    "usage_uuid": usage_uuid,
    "extracted_app_code": None,
    "reason": "NO_APP_CODE_EXTRACTED_FROM_ANY_USAGE_PROPERTY",
}
                    )

            created = write_resolved_batch(session, resolved_rows)
            unresolved = write_unresolved_batch(session, unresolved_rows)

            processed = len(usages)

            total_processed += processed
            total_resolved += created
            total_unresolved += unresolved

            print(
                {
                    "processed": processed,
                    "resolved": created,
                    "unresolved": unresolved,
                }
            )

    driver.close()

    print("Done")
    print("Total processed:", total_processed)
    print("Total resolved:", total_resolved)
    print("Total unresolved:", total_unresolved)


if __name__ == "__main__":
    main()