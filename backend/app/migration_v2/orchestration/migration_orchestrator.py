from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SAFE_PHASES = [
    "register",
    "profile",
    "drift",
    "mapping-plan",
    "preprocess",
    "validate",
    "graph-build",
    "lineage-paths",
    "audit",
    "agent-gate-review",
]

REQUIRED_REPORTS = {
    "registration": "registration_report.json",
    "profile": "profile_report.json",
    "schema_drift": "schema_drift_report.json",
    "mapping": "mapping_plan.json",
    "preprocess": "staging_preprocess_report.json",
    "validation": "validation_report.json",
    "graph_build": "graph_build_report.json",
    "lineage_paths": "lineage_path_report.json",
    "graph_audit": "graph_audit_report.json",
    "relationship_parity": "relationship_parity_report.json",
    "audit_compare": "audit_compare_report.json",
    "duplicate_node_audit": "duplicate_node_audit_report.json",
    "orphan_resolution": "orphan_resolution_report.json",
    "relationship_delta_explanation": "relationship_delta_explanation_report.json",
    "publish_decision_layer": "publish_decision_layer_report.json",
    "validation_queue": "validation_queue_report.json",
    "fast_search_benchmark": "fast_search_benchmark_report.json",
}


def report_root() -> Path:
    return Path(__file__).resolve().parents[4] / "reports" / "migration_v2"


def load_report(export_id: str, filename: str) -> dict[str, Any] | None:
    path = report_root() / export_id / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def gate_item(name: str, status: str, reason: str, evidence: str | None = None) -> dict[str, Any]:
    return {
        "gate": name,
        "status": status,
        "reason": reason,
        "evidence": evidence,
    }


def recommend_gates(export_id: str) -> dict[str, Any]:
    reports = {name: load_report(export_id, filename) for name, filename in REQUIRED_REPORTS.items()}
    gates: list[dict[str, Any]] = []
    missing = [name for name, payload in reports.items() if payload is None]

    if reports["schema_drift"] is None:
        gates.append(gate_item("mapping_drift", "blocked", "Schema drift report is missing.", REQUIRED_REPORTS["schema_drift"]))
    else:
        drift = reports["schema_drift"]
        missing_required = drift.get("missing_required_columns") or drift.get("missing_required") or []
        unexpected = drift.get("unexpected_columns") or []
        if missing_required:
            gates.append(gate_item("mapping_drift", "blocked", "Required contract columns are missing.", REQUIRED_REPORTS["schema_drift"]))
        elif unexpected:
            gates.append(gate_item("mapping_drift", "review", "Unexpected columns are present and should be reviewed.", REQUIRED_REPORTS["schema_drift"]))
        else:
            gates.append(gate_item("mapping_drift", "ready", "No blocking schema drift found.", REQUIRED_REPORTS["schema_drift"]))

    if reports["validation"] is None:
        gates.append(gate_item("staging_validation", "blocked", "Validation report is missing.", REQUIRED_REPORTS["validation"]))
    else:
        validation = reports["validation"]
        severity_counts = validation.get("severity_counts") or {}
        error_count = int(severity_counts.get("ERROR") or severity_counts.get("error") or 0)
        warn_count = int(severity_counts.get("WARN") or severity_counts.get("warning") or 0)
        if error_count:
            gates.append(gate_item("staging_validation", "blocked", f"{error_count} validation errors remain.", REQUIRED_REPORTS["validation"]))
        elif warn_count:
            gates.append(gate_item("staging_validation", "review", f"{warn_count} validation warnings remain.", REQUIRED_REPORTS["validation"]))
        else:
            gates.append(gate_item("staging_validation", "ready", "No validation errors or warnings found.", REQUIRED_REPORTS["validation"]))

    if reports["graph_audit"] is None:
        gates.append(gate_item("graph_audit", "blocked", "Graph audit report is missing.", REQUIRED_REPORTS["graph_audit"]))
    else:
        hierarchy = reports["graph_audit"].get("staging_hierarchy") or {}
        neo4j_graph = reports["graph_audit"].get("neo4j_graph") or {}
        missing_parent_count = int(hierarchy.get("missing_parent_count") or 0)
        cycle_count = int(hierarchy.get("cycle_count") or 0)
        irregular_count = int(hierarchy.get("irregular_allowed_count") or 0)
        actionable_orphan_count = int(neo4j_graph.get("actionable_orphan_count") or 0)
        if cycle_count or missing_parent_count:
            gates.append(
                gate_item(
                    "graph_audit",
                    "blocked",
                    f"Graph audit found {cycle_count} cycles and {missing_parent_count} missing parents.",
                    REQUIRED_REPORTS["graph_audit"],
                )
            )
        elif irregular_count:
            gates.append(
                gate_item(
                    "graph_audit",
                    "review",
                    f"{irregular_count} hierarchy edges are irregular but allowed by v2 policy.",
                    REQUIRED_REPORTS["graph_audit"],
                )
            )
        elif actionable_orphan_count:
            gates.append(
                gate_item(
                    "graph_audit",
                    "review",
                    f"{actionable_orphan_count} orphan nodes need classification or explanation.",
                    REQUIRED_REPORTS["graph_audit"],
                )
            )
        else:
            gates.append(gate_item("graph_audit", "ready", "Graph audit has no blocking hierarchy issues.", REQUIRED_REPORTS["graph_audit"]))

    if reports["lineage_paths"] is None:
        gates.append(gate_item("lineage_paths", "blocked", "Lineage path report is missing.", REQUIRED_REPORTS["lineage_paths"]))
    else:
        path_counts = reports["lineage_paths"].get("path_family_counts") or {}
        required_families = {"catalog_hierarchy", "semantic_implements", "technical_upstream", "technical_downstream", "usage_context"}
        missing_families = sorted(family for family in required_families if not path_counts.get(family))
        if missing_families:
            gates.append(
                gate_item(
                    "lineage_paths",
                    "review",
                    "Some path families have no rows: " + ", ".join(missing_families),
                    REQUIRED_REPORTS["lineage_paths"],
                )
            )
        else:
            gates.append(gate_item("lineage_paths", "ready", "All expected path families are populated.", REQUIRED_REPORTS["lineage_paths"]))

    if reports["audit_compare"] is None:
        gates.append(gate_item("baseline_compare", "blocked", "Baseline comparison report is missing.", REQUIRED_REPORTS["audit_compare"]))
    elif not reports["audit_compare"].get("baseline_report_found"):
        gates.append(gate_item("baseline_compare", "review", "No v0 baseline report was found for comparison.", REQUIRED_REPORTS["audit_compare"]))
    else:
        different = [
            row for row in reports["audit_compare"].get("benchmark_rows", [])
            if row.get("status") == "different" and row.get("metric_name") != "Field_count"
        ]
        if different:
            gates.append(gate_item("baseline_compare", "review", "Some v0/v2 benchmark deltas need explanation.", REQUIRED_REPORTS["audit_compare"]))
        else:
            gates.append(gate_item("baseline_compare", "ready", "Comparable v0/v2 benchmark rows are matched or explainable.", REQUIRED_REPORTS["audit_compare"]))

    if reports["relationship_parity"] is None:
        gates.append(gate_item("relationship_parity", "blocked", "Relationship parity report is missing.", REQUIRED_REPORTS["relationship_parity"]))
    else:
        parity_rows = reports["relationship_parity"].get("rows") or []
        missing = [row for row in parity_rows if row.get("status") == "missing_in_v2"]
        different = [row for row in parity_rows if row.get("status") == "different"]
        if missing:
            gates.append(gate_item("relationship_parity", "blocked", "Some v0 relationship types are missing in v2.", REQUIRED_REPORTS["relationship_parity"]))
        elif different:
            gates.append(gate_item("relationship_parity", "review", "Some v0/v2 relationship counts differ.", REQUIRED_REPORTS["relationship_parity"]))
        else:
            gates.append(gate_item("relationship_parity", "ready", "No missing v0 relationship types in v2 parity report.", REQUIRED_REPORTS["relationship_parity"]))

    hardening_report_gates = [
        ("validation_queue", "validation_queue", "Validation queue report is missing."),
        ("fast_search_benchmark", "fast_search_benchmark", "Fast search benchmark report is missing."),
    ]
    for report_key, gate_name, missing_reason in hardening_report_gates:
        filename = REQUIRED_REPORTS[report_key]
        report = reports[report_key]
        if report is None:
            gates.append(gate_item(gate_name, "blocked", missing_reason, filename))
            continue
        status = report.get("status")
        blockers = report.get("blockers") or []
        if status == "ready" and not blockers:
            gates.append(gate_item(gate_name, "ready", "Hardening report is ready.", filename))
        elif blockers:
            gates.append(
                gate_item(
                    gate_name,
                    "blocked",
                    "; ".join(str(item) for item in blockers[:3]),
                    filename,
                )
            )
        else:
            gates.append(gate_item(gate_name, "review", f"Hardening report status is `{status}`.", filename))

    statuses = [item["status"] for item in gates]
    if "blocked" in statuses:
        recommendation = "blocked"
    elif "review" in statuses:
        recommendation = "human_review"
    else:
        recommendation = "ready_for_publish_review"

    return {
        "export_id": export_id,
        "status": recommendation,
        "safe_phases": SAFE_PHASES,
        "missing_reports": missing,
        "gates": gates,
        "principle": "Agents recommend gates from evidence; deterministic scripts perform database and graph writes.",
    }
