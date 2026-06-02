from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings


settings = get_settings()


def normalize_rel_type(value: str | None) -> str:
    if not value:
        return ""

    mapping = {
        "IsInputOf": "IS_INPUT_OF",
        "IsOutputOf": "IS_OUTPUT_OF",
    }

    return mapping.get(value, value).upper()


def clean_name(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    text = re.sub(r"\s+", " ", text)
    return text


def safe_dataset_name(row: dict[str, Any], side: str) -> str:
    name = (
        clean_name(row.get(f"{side}_name_tech"))
        or clean_name(row.get(f"{side}_name_label"))
        or clean_name(row.get(f"{side}_node_id"))
        or "unknown_dataset"
    )

    return name.replace("\\", "/").strip("/")


def safe_job_name(row: dict[str, Any]) -> str:
    name = (
        clean_name(row.get("tgt_name_tech"))
        or clean_name(row.get("tgt_name_label"))
        or clean_name(row.get("tgt_node_id"))
        or "unknown_job"
    )

    return name.replace("\\", "/").strip("/")


def dataset_from_src(row: dict[str, Any]) -> dict[str, Any]:
    dataset_name = safe_dataset_name(row, "src")

    return {
        "namespace": settings.OPENLINEAGE_DATASET_NAMESPACE,
        "name": dataset_name,
        "facets": {
            "datagalaxy": {
                "_producer": settings.OPENLINEAGE_PRODUCER,
                "_schemaURL": "https://openlineage.io/spec/facets/1-0-0/BaseFacet.json",
                "node_id": row.get("src_node_id"),
                "name_label": row.get("src_name_label"),
                "name_tech": row.get("src_name_tech"),
                "entity_type": row.get("src_entity_type"),
                "data_type": row.get("src_data_type"),
            }
        },
    }


def make_run_id(job_node_id: str, input_ids: list[str], output_ids: list[str]) -> str:
    raw = "|".join(
        [
            "datagalaxy-bootstrap",
            job_node_id,
            ",".join(sorted(input_ids)),
            ",".join(sorted(output_ids)),
        ]
    )

    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


def make_event_time() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def group_links_by_job(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Confirmed from profiling:
    - IsInputOf:  src = input data asset,  tgt = DataProcessing/DataProcessingItem
    - IsOutputOf: src = output data asset, tgt = DataProcessing/DataProcessingItem

    So the job key is always tgt_node_id.
    """

    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "job_row": None,
            "inputs": {},
            "outputs": {},
            "input_rows": [],
            "output_rows": [],
        }
    )

    for row in rows:
        rel_type = normalize_rel_type(row.get("link_type"))
        job_id = row.get("tgt_node_id")
        src_id = row.get("src_node_id")

        if not job_id or not src_id:
            continue

        group = grouped[job_id]

        if group["job_row"] is None:
            group["job_row"] = row

        dataset = dataset_from_src(row)

        if rel_type == "IS_INPUT_OF":
            group["inputs"][dataset["name"]] = dataset
            group["input_rows"].append(row)

        elif rel_type == "IS_OUTPUT_OF":
            group["outputs"][dataset["name"]] = dataset
            group["output_rows"].append(row)

    return grouped


def build_openlineage_event(job_id: str, group: dict[str, Any]) -> dict[str, Any] | None:
    inputs = list(group["inputs"].values())
    outputs = list(group["outputs"].values())
    job_row = group["job_row"]

    if not job_row:
        return None

    if not inputs or not outputs:
        return None

    input_ids = [
        r.get("src_node_id")
        for r in group["input_rows"]
        if r.get("src_node_id")
    ]

    output_ids = [
        r.get("src_node_id")
        for r in group["output_rows"]
        if r.get("src_node_id")
    ]

    job_name = safe_job_name(job_row)

    run_id = make_run_id(
        job_node_id=job_id,
        input_ids=input_ids,
        output_ids=output_ids,
    )

    return {
        "eventType": "COMPLETE",
        "eventTime": make_event_time(),
        "producer": settings.OPENLINEAGE_PRODUCER,
        "schemaURL": "https://openlineage.io/spec/1-0-5/OpenLineage.json",
        "run": {
            "runId": run_id,
            "facets": {
                "datagalaxyBootstrap": {
                    "_producer": settings.OPENLINEAGE_PRODUCER,
                    "_schemaURL": "https://openlineage.io/spec/facets/1-0-0/BaseFacet.json",
                    "mapping_method": "group_by_tgt_node_id",
                    "confidence": "HIGH",
                    "source_relationships": ["IsInputOf", "IsOutputOf"],
                    "job_node_id": job_id,
                    "input_count": len(inputs),
                    "output_count": len(outputs),
                }
            },
        },
        "job": {
            "namespace": settings.OPENLINEAGE_JOB_NAMESPACE,
            "name": job_name,
            "facets": {
                "datagalaxy": {
                    "_producer": settings.OPENLINEAGE_PRODUCER,
                    "_schemaURL": "https://openlineage.io/spec/facets/1-0-0/BaseFacet.json",
                    "node_id": job_id,
                    "name_label": job_row.get("tgt_name_label"),
                    "name_tech": job_row.get("tgt_name_tech"),
                    "entity_type": job_row.get("tgt_entity_type"),
                    "data_type": job_row.get("tgt_data_type"),
                    "path": job_row.get("tgt_path"),
                }
            },
        },
        "inputs": inputs,
        "outputs": outputs,
    }


def map_links_to_openlineage_events(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped = group_links_by_job(rows)

    events = []
    skipped = 0

    for job_id, group in grouped.items():
        event = build_openlineage_event(job_id, group)

        if event is None:
            skipped += 1
            continue

        events.append(event)

    stats = {
        "links_read": len(rows),
        "jobs_detected": len(grouped),
        "events_generated": len(events),
        "skipped_jobs_without_inputs_or_outputs": skipped,
    }

    return events, stats