from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from app.migration_v2.schema_intelligence.models import ColumnNode, SchemaProjection, TableNode


def unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def table_key(source_system: str, canonical_table_name: str) -> str:
    return f"{source_system.strip().lower()}::{canonical_table_name.strip().lower()}"


def column_key(table_identity: str, canonical_name: str, raw_name: str) -> str:
    identity = canonical_name or raw_name
    return f"{table_identity}::{identity.strip().lower()}"


def inverse_column_map(table_contract: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(raw_name): str(canonical_name)
        for canonical_name, raw_name in (table_contract.get("columns") or {}).items()
    }


def column_rules(
    raw_name: str,
    canonical_name: str,
    table_contract: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> list[str]:
    global_rules = contract.get("global_rules") or {}
    typing = contract.get("typing_rules") or {}
    rules: list[str] = []
    if raw_name in set(table_contract.get("required_columns") or []):
        rules.append("required_by_contract")
    if raw_name == global_rules.get("primary_id_column"):
        rules.append("stable_entity_identifier")
    if raw_name == global_rules.get("parent_id_column"):
        rules.append("parent_identifier")
    if raw_name == global_rules.get("status_column"):
        rules.append("status_is_audit_metadata")
    if raw_name in set(global_rules.get("forbidden_join_columns") or []):
        rules.append("forbidden_as_entity_join_key")
    if raw_name in set(typing.get("identifier_columns") or []):
        rules.append("normalize_identifier")
    if raw_name in set(typing.get("boolean_columns") or []):
        rules.append("normalize_boolean")
    if raw_name in set(typing.get("integer_columns") or []):
        rules.append("parse_integer")
    if raw_name in set(typing.get("numeric_columns") or []):
        rules.append("parse_numeric")
    if canonical_name in {"created_at", "updated_at", "validated_at", "export_date"}:
        rules.append("parse_date_with_contract_formats")
    if (contract.get("unknown_columns_policy") or {}).get("preserve_unknown_columns"):
        rules.append("preserve_raw_evidence")
    return unique_strings(rules)


def declared_types(raw_name: str, contract: Mapping[str, Any]) -> list[str]:
    typing = contract.get("typing_rules") or {}
    result: list[str] = []
    if raw_name in set(typing.get("boolean_columns") or []):
        result.append("contract:boolean")
    if raw_name in set(typing.get("integer_columns") or []):
        result.append("contract:integer")
    if raw_name in set(typing.get("numeric_columns") or []):
        result.append("contract:numeric")
    if raw_name in set(typing.get("identifier_columns") or []):
        result.append("contract:identifier")
    return result


def generated_description(raw_table: str, raw_name: str, canonical_name: str, present: bool) -> str:
    if canonical_name and canonical_name != raw_name:
        prefix = f"Canonical `{canonical_name}` column mapped from raw `{raw_name}`"
    else:
        prefix = f"Raw `{raw_name}` column"
    state = "observed" if present else "declared by contract but not observed"
    return f"{prefix} in `{raw_table}`; {state} in the latest export."


def build_schema_projection(
    *,
    export_id: str,
    contract: Mapping[str, Any],
    profiles: Iterable[Mapping[str, Any]],
    mapping_decisions: Iterable[Mapping[str, Any]],
    raw_files: Iterable[Mapping[str, Any]],
    source_system: str = "datagalaxy_athena",
) -> SchemaProjection:
    contract_version = str(contract.get("contract_version") or "unknown")
    contracts = contract.get("tables") or {}
    profiles_by_table: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in profiles:
        profiles_by_table[str(row["raw_table_name"])][str(row["column_name"])] = dict(row)

    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    for row in mapping_decisions:
        table = str(row.get("raw_table_name") or "")
        raw_name = str(row.get("raw_column_name") or "")
        key = (table, raw_name)
        current = decisions.get(key)
        if current is None or int(row.get("id") or 0) >= int(current.get("id") or 0):
            decisions[key] = dict(row)

    files_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in raw_files:
        files_by_table[str(row["raw_table_name"])].append(dict(row))

    raw_tables = sorted(set(contracts) | set(profiles_by_table) | set(files_by_table))
    table_nodes: list[TableNode] = []
    column_nodes: list[ColumnNode] = []

    for raw_table in raw_tables:
        table_contract = dict(contracts.get(raw_table) or {})
        canonical_table = str(table_contract.get("canonical_table") or raw_table)
        t_key = table_key(source_system, canonical_table)
        profile_rows = profiles_by_table.get(raw_table, {})
        column_map = inverse_column_map(table_contract)
        expected_raw_columns = set(column_map)
        all_raw_columns = sorted(set(profile_rows) | expected_raw_columns)
        files = files_by_table.get(raw_table, [])

        table_nodes.append(
            TableNode(
                table_key=t_key,
                table_name=canonical_table,
                canonical_table_name=canonical_table,
                name_variants=unique_strings([canonical_table, raw_table]),
                description=str(
                    table_contract.get("description")
                    or f"Schema metadata for `{canonical_table}` from DataGalaxy/Athena export table `{raw_table}`."
                ),
                source_system=source_system,
                object_type=table_contract.get("object_type"),
                relationship_table=bool(table_contract.get("relationship_table")),
                required_columns=unique_strings(table_contract.get("required_columns") or []),
                export_ids=[export_id],
                contract_versions=[contract_version],
                file_paths=unique_strings(row.get("file_path") for row in files),
                file_hashes=unique_strings(row.get("file_hash") for row in files),
                observed_column_count=len(profile_rows),
                expected_column_count=len(expected_raw_columns),
                first_seen_export=export_id,
                last_seen_export=export_id,
            )
        )

        for raw_name in all_raw_columns:
            profile = profile_rows.get(raw_name)
            canonical_name = column_map.get(raw_name, raw_name)
            decision = decisions.get((raw_table, raw_name), {})
            present = profile is not None
            observed_type = profile.get("data_type_guess") if profile else None
            descriptions = table_contract.get("column_descriptions") or {}
            explicit_description = descriptions.get(canonical_name) or descriptions.get(raw_name)
            confidence = decision.get("confidence")
            try:
                confidence_value = float(confidence) if confidence is not None else (1.0 if raw_name in column_map else 0.5)
            except (TypeError, ValueError):
                confidence_value = 0.0
            null_count = int(profile["null_count"]) if profile and profile.get("null_count") is not None else None
            non_null_count = (
                int(profile["non_null_count"])
                if profile and profile.get("non_null_count") is not None
                else None
            )
            column_nodes.append(
                ColumnNode(
                    column_key=column_key(t_key, canonical_name, raw_name),
                    table_key=t_key,
                    column_name=canonical_name,
                    canonical_column_name=canonical_name,
                    raw_column_name=raw_name,
                    name_variants=unique_strings([canonical_name, raw_name]),
                    description=str(
                        explicit_description
                        or generated_description(raw_table, raw_name, canonical_name, present)
                    ),
                    description_source="contract" if explicit_description else "deterministic_mapping_summary",
                    observed_types=unique_strings(
                        ([f"profile:{observed_type}"] if observed_type else [])
                        + declared_types(raw_name, contract)
                    ),
                    rules=column_rules(raw_name, canonical_name, table_contract, contract),
                    warnings=unique_strings((profile or {}).get("warnings") or []),
                    sample_values=unique_strings((profile or {}).get("sample_values") or []),
                    export_ids=[export_id] if present else [],
                    contract_versions=[contract_version],
                    source_system=source_system,
                    mapping_decision=str(
                        decision.get("decision_type")
                        or ("contract_mapping" if raw_name in column_map else "unmapped_observation")
                    ),
                    mapping_confidence=max(0.0, min(1.0, confidence_value)),
                    requires_human_approval=bool(decision.get("requires_human_approval")),
                    required_by_contract=raw_name in set(table_contract.get("required_columns") or []),
                    present_in_latest_export=present,
                    nullable_in_latest_export=(null_count > 0) if null_count is not None else None,
                    null_count=null_count,
                    non_null_count=non_null_count,
                    distinct_count=(
                        int(profile["distinct_count"])
                        if profile and profile.get("distinct_count") is not None
                        else None
                    ),
                    first_seen_export=export_id,
                    last_seen_export=export_id,
                )
            )

    return SchemaProjection(
        export_id=export_id,
        contract_version=contract_version,
        source_system=source_system,
        tables=table_nodes,
        columns=column_nodes,
    )
