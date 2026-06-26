from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text

from _common import (
    clean_record,
    clean_value,
    ensure_tables,
    fetch_raw_files,
    json_param,
    load_contract,
    path_hash,
    postgres_engine,
    read_frame,
    setup_logging,
    table_contracts,
    write_json_report,
    write_markdown_report,
)


LOGGER = setup_logging("migration_v2.preprocess_to_staging")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preprocess raw DataGalaxy export files into canonical staging.")
    parser.add_argument("--export-id", required=True, help="Export identifier registered by 01_register_export.py.")
    parser.add_argument("--contract", required=True, help="Path to the migration_v2 mapping contract.")
    return parser.parse_args()


def normalize_relationship(value: Any, relationship_mappings: dict[str, Any]) -> tuple[str, str | None]:
    link_type = clean_value(value)
    if link_type and str(link_type) in relationship_mappings:
        mapping = relationship_mappings[str(link_type)]
        return str(mapping["canonical_type"]), mapping.get("family")
    if not link_type:
        return "IS_LINKED_TO", "unknown"
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(link_type)).strip("_").upper()
    return normalized or "IS_LINKED_TO", "generic"


def normalize_object_type(entity_type: Any, data_type: Any, fallback: str) -> str:
    text = str(entity_type or data_type or fallback).strip().lower().replace("_", " ")
    known = {
        "business term": "BusinessTerm",
        "businessterm": "BusinessTerm",
        "data processing": "DataProcessing",
        "dataprocessing": "DataProcessing",
        "data processing item": "DataProcessingItem",
        "dataprocessingitem": "DataProcessingItem",
        "source": "Source",
        "container": "Container",
        "structure": "Structure",
        "field": "Field",
        "column": "Field",
        "table": "Structure",
        "topic": "Structure",
    }
    if text in known:
        return known[text]
    compact = re.sub(r"[^A-Za-z0-9]+", "", str(entity_type or data_type or fallback))
    return compact[:1].upper() + compact[1:] if compact else fallback


def unknown_columns(record: dict[str, Any], mapped_raw_columns: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in mapped_raw_columns and not key.startswith("_migration_v2_")
    }


def canonical_record(record: dict[str, Any], column_map: dict[str, str]) -> dict[str, Any]:
    return {field: clean_value(record.get(raw_column)) for field, raw_column in column_map.items()}


def graph_eligible(has_required_ids: bool) -> tuple[bool, str | None]:
    if not has_required_ids:
        return False, "missing_required_identifier"
    return True, None


def upsert_lineage_endpoint(
    conn,
    export_id: str,
    raw_table_name: str,
    node_id: Any,
    object_type: str,
    name_label: Any,
    name_tech: Any,
    path_full: Any,
    entity_type: Any,
    data_type: Any,
    record: dict[str, Any],
) -> bool:
    node_id = clean_value(node_id)
    if not node_id:
        return False
    conn.execute(
        text(
            """
            INSERT INTO catalog_object_staging(
                export_id, node_id, object_type, name_label, name_tech,
                path_full, path_hash, entity_type, data_type, source_table,
                raw_payload, unknown_columns, is_graph_eligible,
                graph_exclusion_reason
            )
            VALUES (
                :export_id, :node_id, :object_type, :name_label, :name_tech,
                :path_full, :path_hash, :entity_type, :data_type, :source_table,
                CAST(:raw_payload AS jsonb), '{}'::jsonb, true, NULL
            )
            ON CONFLICT (export_id, node_id, object_type) DO UPDATE
            SET name_label = coalesce(catalog_object_staging.name_label, EXCLUDED.name_label),
                name_tech = coalesce(catalog_object_staging.name_tech, EXCLUDED.name_tech),
                path_full = coalesce(catalog_object_staging.path_full, EXCLUDED.path_full),
                path_hash = coalesce(catalog_object_staging.path_hash, EXCLUDED.path_hash),
                entity_type = coalesce(catalog_object_staging.entity_type, EXCLUDED.entity_type),
                data_type = coalesce(catalog_object_staging.data_type, EXCLUDED.data_type),
                raw_payload = catalog_object_staging.raw_payload || EXCLUDED.raw_payload,
                is_graph_eligible = true,
                graph_exclusion_reason = NULL
            """
        ),
        {
            "export_id": export_id,
            "node_id": node_id,
            "object_type": object_type,
            "name_label": clean_value(name_label),
            "name_tech": clean_value(name_tech),
            "path_full": clean_value(path_full),
            "path_hash": path_hash(clean_value(path_full)),
            "entity_type": clean_value(entity_type),
            "data_type": clean_value(data_type),
            "source_table": raw_table_name,
            "raw_payload": json_param(record),
        },
    )
    return True


def insert_object(conn, export_id: str, raw_table_name: str, table_contract: dict[str, Any], record: dict[str, Any]) -> bool:
    column_map = table_contract.get("columns") or {}
    canonical = canonical_record(record, column_map)
    node_id = canonical.get("node_id")
    if not node_id:
        return False

    mapped_raw_columns = set(column_map.values())
    object_type = table_contract.get("object_type") or table_contract.get("canonical_table") or raw_table_name
    return_payload = {
        "export_id": export_id,
        "node_id": node_id,
        "parent_node_id": canonical.get("parent_node_id"),
        "object_type": object_type,
        "name_label": canonical.get("name_label"),
        "name_tech": canonical.get("name_tech"),
        "path_full": canonical.get("path_full"),
        "path_hash": path_hash(canonical.get("path_full")),
        "entity_type": canonical.get("entity_type"),
        "data_type": canonical.get("data_type"),
        "status": canonical.get("status"),
        "app_code": canonical.get("app_code"),
        "source_table": raw_table_name,
        "raw_payload": json_param(record),
        "unknown_columns": json_param(unknown_columns(record, mapped_raw_columns)),
        "is_graph_eligible": bool(record.get("_migration_v2_graph_eligible")),
        "graph_exclusion_reason": record.get("_migration_v2_graph_exclusion_reason"),
    }
    conn.execute(
        text(
            """
            INSERT INTO catalog_object_staging(
                export_id, node_id, parent_node_id, object_type, name_label, name_tech,
                path_full, path_hash, entity_type, data_type, status, app_code,
                source_table, raw_payload, unknown_columns, is_graph_eligible,
                graph_exclusion_reason
            )
            VALUES (
                :export_id, :node_id, :parent_node_id, :object_type, :name_label, :name_tech,
                :path_full, :path_hash, :entity_type, :data_type, :status, :app_code,
                :source_table, CAST(:raw_payload AS jsonb), CAST(:unknown_columns AS jsonb),
                :is_graph_eligible, :graph_exclusion_reason
            )
            ON CONFLICT (export_id, node_id, object_type) DO UPDATE
            SET parent_node_id = EXCLUDED.parent_node_id,
                name_label = EXCLUDED.name_label,
                name_tech = EXCLUDED.name_tech,
                path_full = EXCLUDED.path_full,
                path_hash = EXCLUDED.path_hash,
                entity_type = EXCLUDED.entity_type,
                data_type = EXCLUDED.data_type,
                status = EXCLUDED.status,
                app_code = EXCLUDED.app_code,
                raw_payload = EXCLUDED.raw_payload,
                unknown_columns = EXCLUDED.unknown_columns,
                is_graph_eligible = EXCLUDED.is_graph_eligible,
                graph_exclusion_reason = EXCLUDED.graph_exclusion_reason
            """
        ),
        return_payload,
    )
    return True


def insert_relationship(
    conn,
    export_id: str,
    raw_table_name: str,
    table_contract: dict[str, Any],
    relationship_mappings: dict[str, Any],
    record: dict[str, Any],
) -> bool:
    column_map = table_contract.get("columns") or {}
    canonical = canonical_record(record, column_map)
    src_node_id = canonical.get("src_node_id")
    tgt_node_id = canonical.get("tgt_node_id")
    if not src_node_id or not tgt_node_id:
        return False

    relationship_type, family = normalize_relationship(canonical.get("link_type"), relationship_mappings)
    mapped_raw_columns = set(column_map.values())
    src_type = normalize_object_type(canonical.get("src_entity_type"), canonical.get("src_data_type"), "LineageNode")
    tgt_type = normalize_object_type(canonical.get("tgt_entity_type"), canonical.get("tgt_data_type"), "LineageNode")
    upsert_lineage_endpoint(
        conn,
        export_id,
        raw_table_name,
        src_node_id,
        src_type,
        canonical.get("src_name_label"),
        canonical.get("src_name_tech"),
        None,
        canonical.get("src_entity_type"),
        canonical.get("src_data_type"),
        record,
    )
    upsert_lineage_endpoint(
        conn,
        export_id,
        raw_table_name,
        tgt_node_id,
        tgt_type,
        canonical.get("tgt_name_label"),
        canonical.get("tgt_name_tech"),
        canonical.get("tgt_path"),
        canonical.get("tgt_entity_type"),
        canonical.get("tgt_data_type"),
        record,
    )
    conn.execute(
        text(
            """
            INSERT INTO catalog_relationship_staging(
                export_id, src_node_id, tgt_node_id, relationship_type, relationship_family,
                source_table, link_type, status, raw_payload, unknown_columns,
                is_graph_eligible, graph_exclusion_reason
            )
            VALUES (
                :export_id, :src_node_id, :tgt_node_id, :relationship_type, :relationship_family,
                :source_table, :link_type, :status, CAST(:raw_payload AS jsonb), CAST(:unknown_columns AS jsonb),
                :is_graph_eligible, :graph_exclusion_reason
            )
            """
        ),
        {
            "export_id": export_id,
            "src_node_id": src_node_id,
            "tgt_node_id": tgt_node_id,
            "relationship_type": relationship_type,
            "relationship_family": family,
            "source_table": raw_table_name,
            "link_type": canonical.get("link_type"),
            "status": canonical.get("status"),
            "raw_payload": json_param(record),
            "unknown_columns": json_param(unknown_columns(record, mapped_raw_columns)),
            "is_graph_eligible": True,
            "graph_exclusion_reason": None,
        },
    )
    return True


def main() -> None:
    args = parse_args()
    contract = load_contract(args.contract)
    engine = postgres_engine()
    ensure_tables(engine, ["migration_raw_file", "catalog_object_staging", "catalog_relationship_staging"])
    raw_files = fetch_raw_files(engine, args.export_id)
    tables = table_contracts(contract)
    relationship_mappings = contract.get("relationship_mappings") or {}

    stats = {"objects_inserted": 0, "relationships_inserted": 0, "rows_skipped": 0, "files_processed": 0}
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM catalog_relationship_staging WHERE export_id = :export_id"), {"export_id": args.export_id})
        conn.execute(text("DELETE FROM catalog_object_staging WHERE export_id = :export_id"), {"export_id": args.export_id})

        for raw_file in raw_files:
            raw_table_name = raw_file["raw_table_name"]
            table_contract = tables.get(raw_table_name)
            if not table_contract:
                LOGGER.warning("Skipping uncontracted raw table %s", raw_table_name)
                continue
            frame = read_frame(Path(raw_file["file_path"]))
            stats["files_processed"] += 1
            for raw_record in frame.to_dict(orient="records"):
                record = clean_record(raw_record)
                if table_contract.get("relationship_table"):
                    inserted = insert_relationship(
                        conn,
                        args.export_id,
                        raw_table_name,
                        table_contract,
                        relationship_mappings,
                        record,
                    )
                    stats["relationships_inserted"] += int(inserted)
                    stats["rows_skipped"] += int(not inserted)
                    continue

                column_map = table_contract.get("columns") or {}
                canonical = canonical_record(record, column_map)
                eligible, reason = graph_eligible(bool(canonical.get("node_id")))
                record["_migration_v2_graph_eligible"] = eligible
                record["_migration_v2_graph_exclusion_reason"] = reason
                inserted = insert_object(conn, args.export_id, raw_table_name, table_contract, record)
                stats["objects_inserted"] += int(inserted)
                stats["rows_skipped"] += int(not inserted)

    payload = {"export_id": args.export_id, "contract": args.contract, "stats": stats}
    json_path = write_json_report(args.export_id, "staging_preprocess_report.json", payload)
    md_path = write_markdown_report(
        args.export_id,
        "staging_preprocess_report.md",
        "Migration V2 Staging Preprocess Report",
        [("Summary", "\n".join(f"- `{key}`: {value}" for key, value in stats.items()))],
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
