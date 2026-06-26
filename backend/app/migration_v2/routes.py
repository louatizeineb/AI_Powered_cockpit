from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.migration_v2.agents.base import call_chat_llm, compact_json, llm_config_status, parse_json_object
from app.migration_v2.governance.service import (
    activity,
    agent_evaluations,
    decide_queue_issue,
    export_overview,
    list_exports,
    load_report,
    provenance,
    schema_summary,
    validation_queue,
)
from app.migration_v2.orchestration.persistent_orchestrator import PersistentSchemaOrchestrator
from app.migration_v2.orchestration.repository import WorkflowRepository
from app.migration_v2.orchestration.tool_runtime import AllowlistedToolRuntime


router = APIRouter(prefix="/migration-v2", tags=["Migration V2 Orchestration"])
ROOT = Path(__file__).resolve().parents[3]
_candidate_search_cache: dict[tuple[str, str, int, str], dict[str, Any]] = {}


class StartWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_by: str = Field(default="api", min_length=1)
    require_llm: bool = False
    refresh_tools: bool = False


class ResumeWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    decided_by: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    resolutions: list[dict[str, Any]] = Field(default_factory=list)


class QueueDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    decided_by: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class GovernanceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_by: str = Field(min_length=1)
    api_base_url: str = "http://127.0.0.1:8001"


class AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class GovernanceAssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    screen: str | None = Field(default=None, max_length=80)
    subject: str | None = Field(default=None, max_length=500)
    selected_item: dict[str, Any] | None = None
    history: list[AssistantMessage] = Field(default_factory=list, max_length=8)
    use_llm: bool = True


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def migration_postgres_url() -> str:
    settings = get_settings()
    env_config_path = resolve_path(settings.migration_v2_env_config_path)
    postgres_url = settings.migration_v2_postgres_url
    if not postgres_url and env_config_path.exists():
        try:
            raw = env_config_path.read_text(encoding="utf-8")
            try:
                config = json.loads(raw)
            except json.JSONDecodeError:
                import yaml

                config = yaml.safe_load(raw)
            postgres_url = str((config.get("v2") or {}).get("postgres_url") or "")
        except (OSError, TypeError, ValueError):
            postgres_url = ""
    if not postgres_url:
        raise HTTPException(status_code=503, detail="Migration V2 PostgreSQL is not configured.")
    return postgres_url


def migration_engine():
    return create_engine(migration_postgres_url(), pool_pre_ping=True)


def orchestrator(*, require_llm: bool = False, refresh_tools: bool = False) -> PersistentSchemaOrchestrator:
    settings = get_settings()
    env_config_path = resolve_path(settings.migration_v2_env_config_path)
    contract_path = resolve_path(settings.migration_v2_contract_path)
    postgres_url = migration_postgres_url()
    return PersistentSchemaOrchestrator(
        engine=create_engine(postgres_url, pool_pre_ping=True),
        postgres_url=postgres_url,
        env_config_path=str(env_config_path),
        contract_path=str(contract_path),
        require_llm=require_llm,
        refresh_tools=refresh_tools,
    )


@router.post("/workflows/{export_id}/start")
def start_workflow(export_id: str, payload: StartWorkflowRequest | None = None):
    payload = payload or StartWorkflowRequest()
    try:
        return orchestrator(
            require_llm=payload.require_llm,
            refresh_tools=payload.refresh_tools,
        ).start(export_id, created_by=payload.created_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Workflow start failed: {exc}") from exc


@router.get("/workflows/{run_id}")
def workflow_status(run_id: str):
    try:
        return orchestrator().status(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Workflow status failed: {exc}") from exc


@router.post("/workflows/{run_id}/resume")
def resume_workflow(run_id: str, payload: ResumeWorkflowRequest):
    try:
        return orchestrator().resume(run_id, payload.model_dump(mode="json"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Workflow resume failed: {exc}") from exc


@router.get("/exports/{export_id}/publication-readiness")
def publication_readiness(export_id: str):
    with migration_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, policy_version, status, object_counts, relationship_counts,
                       hard_blockers, rollback_metadata, evidence, created_by, created_at
                FROM migration_publication_snapshot
                WHERE export_id = :export_id
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"export_id": export_id},
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="No conditional publication snapshot exists for this export.")
    return dict(row)


@router.get("/exports/{export_id}/governance-items")
def governance_items(
    export_id: str,
    state: str = Query(default="quarantine", pattern="^(quarantine|review_pending|excluded|repair|hard_block)$"),
    kind: str = Query(default="object", pattern="^(object|relationship)$"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    table = "catalog_object_staging" if kind == "object" else "catalog_relationship_staging"
    identity = (
        "node_id, parent_node_id, object_type"
        if kind == "object"
        else "id, src_node_id, tgt_node_id, relationship_type"
    )
    with migration_engine().connect() as conn:
        total = int(conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE export_id = :export_id AND publication_state = CAST(:state AS migration_publication_state)"),
            {"export_id": export_id, "state": state},
        ).scalar_one())
        rows = conn.execute(
            text(
                f"""
                SELECT {identity}, publication_state::text, publication_reason,
                       publication_policy_version, publication_decided_by,
                       publication_decided_at, publication_evidence
                FROM {table}
                WHERE export_id = :export_id
                  AND publication_state = CAST(:state AS migration_publication_state)
                ORDER BY id LIMIT :limit OFFSET :offset
                """
            ),
            {"export_id": export_id, "state": state, "limit": limit, "offset": offset},
        ).mappings().all()
    return {"export_id": export_id, "state": state, "kind": kind, "total": total, "items": [dict(row) for row in rows]}


@router.get("/exports")
def exports():
    return {"items": list_exports(migration_engine())}


@router.get("/exports/{export_id}/overview")
def overview(export_id: str):
    try:
        return export_overview(migration_engine(), export_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Export not found: {export_id}") from exc


@router.get("/exports/{export_id}/validation-queue")
def queue_items(
    export_id: str,
    status: str | None = None,
    issue_type: str | None = None,
    publish_policy: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    return validation_queue(
        migration_engine(),
        export_id,
        status=status,
        issue_type=issue_type,
        publish_policy=publish_policy,
        limit=limit,
        offset=offset,
    )


@router.post("/exports/{export_id}/validation-queue/{issue_id}/decision")
def decide_issue(export_id: str, issue_id: str, payload: QueueDecisionRequest):
    try:
        return decide_queue_issue(
            migration_engine(), export_id, issue_id,
            decision=payload.decision, decided_by=payload.decided_by, rationale=payload.rationale,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Pending validation issue not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/exports/{export_id}/activity")
def migration_activity(export_id: str, limit: int = Query(default=100, ge=1, le=500)):
    return activity(migration_engine(), export_id, limit)


@router.get("/exports/{export_id}/agent-evaluations")
def migration_agent_evaluations(export_id: str, limit: int = Query(default=100, ge=1, le=500)):
    return agent_evaluations(migration_engine(), export_id, limit)


@router.get("/exports/{export_id}/schema-intelligence")
def migration_schema_intelligence(export_id: str):
    return schema_summary(migration_engine(), export_id)


@router.get("/exports/{export_id}/schema-intelligence/{raw_table_name}/columns")
def migration_schema_columns(export_id: str, raw_table_name: str):
    with migration_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT column_name, data_type_guess, null_count, distinct_count, non_null_count,
                   sample_values, warnings, created_at
            FROM migration_column_profile
            WHERE export_id = :export_id AND raw_table_name = :raw_table_name
            ORDER BY column_name
        """), {"export_id": export_id, "raw_table_name": raw_table_name}).mappings().all()
    return {"table": raw_table_name, "columns": [dict(row) for row in rows]}


def _safe_count_map(rows: list[dict[str, Any]], key: str, value_key: str = "count") -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        name = str(row.get(key) or "unknown")
        result[name] = result.get(name, 0) + int(row.get(value_key) or 0)
    return result


def _assistant_citations(
    export_id: str,
    overview_payload: dict[str, Any],
    queue_payload: dict[str, Any],
    activity_payload: dict[str, Any],
    provenance_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = [
        {
            "id": "overview",
            "label": "Release overview",
            "type": "overview",
            "subject": export_id,
        }
    ]
    for item in queue_payload.get("items", [])[:6]:
        citations.append({
            "id": f"queue:{item.get('issue_id')}",
            "label": item.get("issue_type") or item.get("issue_id"),
            "type": "validation_queue",
            "subject": item.get("issue_id"),
            "status": item.get("queue_status"),
            "policy": item.get("publish_policy"),
        })
    for item in activity_payload.get("tool_executions", [])[:4]:
        citations.append({
            "id": f"tool:{item.get('execution_id')}",
            "label": item.get("tool_name"),
            "type": "tool_execution",
            "subject": item.get("execution_id"),
            "status": item.get("status"),
        })
    for item in provenance_events[:6]:
        citations.append({
            "id": item.get("event_id"),
            "label": item.get("event_type"),
            "type": "provenance",
            "subject": item.get("subject_id"),
            "status": item.get("status"),
            "occurred_at": item.get("occurred_at"),
        })
    return citations


def _selected_item_identity(selected_item: dict[str, Any]) -> str:
    return str(
        selected_item.get("issue_id")
        or selected_item.get("node_id")
        or selected_item.get("raw_table_name")
        or selected_item.get("execution_id")
        or selected_item.get("event_id")
        or "selected item"
    )


def _selected_item_policy(selected_item: dict[str, Any]) -> str:
    return str(
        selected_item.get("agent_proposed_policy")
        or selected_item.get("publish_policy")
        or selected_item.get("proposed_action")
        or "needs_human"
    ).lower()


def _plain_policy_effect(policy: str) -> str:
    return {
        "accept": "Approve it for the trusted graph after the reviewer is comfortable with the evidence.",
        "quarantine": "Keep it as evidence, but hide it from normal search and lineage until the uncertainty is resolved.",
        "exclude": "Retain the record for audit evidence, but remove it from the trusted projection.",
        "repair": "Keep it blocking until a deterministic repair or repair evidence is recorded.",
        "resolved": "Treat the repair as accepted, then recalculate readiness.",
        "block": "Stop publication until a human changes the decision.",
        "needs_human": "Leave it pending until a reviewer chooses accept, quarantine, remove, repair, or block.",
    }.get(policy, "Review the evidence before assigning a publish policy.")


def _selected_item_answer_lines(selected_item: dict[str, Any]) -> list[str]:
    identity = _selected_item_identity(selected_item)
    issue_type = str(selected_item.get("issue_type") or selected_item.get("relationship_type") or "governance issue")
    status = selected_item.get("queue_status") or selected_item.get("status") or selected_item.get("publication_state") or "unknown"
    policy = _selected_item_policy(selected_item)
    confidence = selected_item.get("agent_confidence", selected_item.get("confidence"))
    confidence_text = ""
    if confidence not in (None, ""):
        try:
            confidence_text = f" Confidence is about {round(float(confidence) * 100)}%."
        except (TypeError, ValueError):
            confidence_text = f" Confidence is `{confidence}`."
    rationale = (
        selected_item.get("agent_rationale")
        or selected_item.get("rationale")
        or selected_item.get("publication_reason")
        or ""
    )
    missing = selected_item.get("agent_missing_evidence") or selected_item.get("missing_evidence") or []
    if isinstance(missing, str):
        missing = [missing]
    lines = [
        f"Selected issue `{identity}` is a `{issue_type}` item with status `{status}`. The current safest policy is `{policy}`.{confidence_text}",
        f"What that means: {_plain_policy_effect(policy)}",
    ]
    if rationale:
        lines.append(f"Why: {rationale}")
    if missing:
        lines.append("Evidence still needed: " + "; ".join(str(item) for item in missing[:5]) + ".")
    return lines


def _next_step_lines(
    *,
    selected_item: dict[str, Any] | None,
    hard_blockers: list[Any],
    pending_count: int,
    benchmark_status: str,
    publish_status: str,
) -> list[str]:
    if selected_item:
        policy = _selected_item_policy(selected_item)
        if policy == "accept":
            return [
                "Next steps:",
                "1. Confirm the rationale and evidence on the selected issue.",
                "2. Click Accept into trusted only if the evidence is enough for users to see this in the trusted graph.",
                "3. Recalculate readiness, then validate the trusted graph before publish review.",
            ]
        if policy == "quarantine":
            return [
                "Next steps:",
                "1. Confirm the uncertainty is bounded and does not need to appear in normal search or lineage.",
                "2. Click Keep quarantined with a reviewer note that explains the source-quality exception.",
                "3. Recalculate readiness and verify the quarantine view excludes it from the trusted projection.",
            ]
        if policy == "exclude":
            return [
                "Next steps:",
                "1. Confirm the item should be retained only as audit evidence.",
                "2. Click Remove from trusted with the reason.",
                "3. Recalculate readiness and validate the trusted graph.",
            ]
        if policy == "repair":
            return [
                "Next steps:",
                "1. Do not accept it as trusted yet.",
                "2. Run or inspect the repair evidence named by the issue.",
                "3. Mark repaired only after the deterministic repair evidence is present, then recalculate readiness.",
            ]
        if policy == "block":
            return [
                "Next steps:",
                "1. Leave publish blocked until the owner answers the question.",
                "2. Add the blocking rationale to the decision note.",
                "3. Recalculate readiness after the human decision changes.",
            ]
        return [
            "Next steps:",
            "1. Inspect the selected evidence and missing-evidence fields.",
            "2. Choose accept, quarantine, remove, repair, or block based on publish risk.",
            "3. Recalculate readiness after recording the decision.",
        ]

    if hard_blockers:
        return [
            "Next steps:",
            "1. Open Review issues and handle the hard blockers first.",
            "2. Recalculate readiness after each set of decisions.",
            "3. Validate the trusted graph before returning to publish.",
        ]
    if pending_count:
        return [
            "Next steps:",
            "1. Open Review issues and clear the pending decision queue.",
            "2. Prefer quarantine for bounded uncertainty and repair for structural fixes.",
            "3. Recalculate readiness when the queue decisions are recorded.",
        ]
    if benchmark_status != "ready":
        return [
            "Next steps:",
            "1. Open Release checks.",
            "2. Refresh the candidate search index.",
            "3. Run the benchmark and confirm it passes before publish dry-run.",
        ]
    if publish_status not in {"ready", "ready_to_publish"}:
        return [
            "Next steps:",
            "1. Open Publish.",
            "2. Run publish dry-run.",
            "3. Review any reported blockers before activating the trusted graph.",
        ]
    return [
        "Next steps:",
        "1. Open Publish review.",
        "2. Confirm the approver and final evidence.",
        "3. Publish the trusted graph only after explicit approval.",
    ]


def _deterministic_governance_answer(
    *,
    export_id: str,
    message: str,
    screen: str | None,
    selected_item: dict[str, Any] | None,
    overview_payload: dict[str, Any],
    queue_payload: dict[str, Any],
    activity_payload: dict[str, Any],
    schema_payload: dict[str, Any],
) -> str:
    publication = overview_payload.get("publication") or {}
    workflow = overview_payload.get("workflow") or {}
    queue_counts = _safe_count_map(overview_payload.get("queue_counts") or [], "queue_status")
    object_counts = publication.get("object_counts") or {}
    relationship_counts = publication.get("relationship_counts") or {}
    hard_blockers = publication.get("hard_blockers") or []
    benchmark = overview_payload.get("benchmark") or {}
    benchmark_status = str((benchmark or {}).get("status") or "").lower()
    publish_report = overview_payload.get("publish_report") or {}
    publish_status = str((publish_report or {}).get("status") or "").lower()
    search_state = overview_payload.get("search_state") or {}
    lower = message.lower()
    pending_count = int(queue_counts.get("pending") or 0)

    lines = [
        f"For export `{export_id}`, the workflow is `{workflow.get('status') or 'unknown'}` "
        f"at phase `{workflow.get('current_phase') or 'not started'}`.",
    ]
    if publication:
        lines.append(
            "The latest publication snapshot is "
            f"`{publication.get('status')}` with "
            f"{object_counts.get('trusted', 0)} trusted objects, "
            f"{object_counts.get('quarantine', 0)} quarantined objects, "
            f"{relationship_counts.get('trusted', 0)} trusted relationships, and "
            f"{len(hard_blockers)} hard blocker(s)."
        )
    else:
        lines.append("No conditional publication snapshot exists yet, so run the policy/projection step before judging publish readiness.")

    if selected_item:
        lines.extend(_selected_item_answer_lines(selected_item))

    if "queue" in lower or "decision" in lower or screen == "validation":
        lines.append(
            "The Decision Inbox is where anomalies become explicit governance decisions. "
            f"Right now it has {queue_payload.get('total', 0)} listed item(s); "
            f"queue status counts are {queue_counts or {'none': 0}}."
        )
        lines.append(
            "Accept moves approved evidence into the trusted slice, quarantine keeps evidence but hides it from normal search/lineage, "
            "repair remains blocking until deterministic repair evidence exists, and block creates a hard publication stop."
        )
    elif "publish" in lower or "release" in lower or screen == "publish":
        lines.append(
            "Dry-run checks whether publication would pass without activating anything. "
            "Publish requires an approver and promotes the trusted candidate graph/search version only if gates still pass."
        )
    elif "schema" in lower or screen == "schema":
        lines.append(
            "The Schema screen shows the Schema Intelligence KG: tables are central nodes, columns are connected by HAS_COLUMN, "
            f"and column details live as metadata. Current summary: {len(schema_payload.get('tables') or [])} table(s), "
            f"{len(schema_payload.get('mapping_proposals') or [])} mapping proposal(s)."
        )
    elif "agent" in lower or screen == "agents":
        lines.append(
            "Agents are controlled workers, not free operators. "
            f"The activity log currently shows {len(activity_payload.get('agent_runs') or [])} agent run(s), "
            f"{len(activity_payload.get('tool_executions') or [])} allowlisted tool execution(s), and "
            f"{len(activity_payload.get('approvals') or [])} approval interrupt(s)."
        )
    elif "search" in lower or "benchmark" in lower or screen == "benchmark":
        acceptance = benchmark.get("acceptance") if isinstance(benchmark, dict) else {}
        lines.append(
            "Search readiness checks the candidate search index before publish. "
            f"Active graph version is {search_state.get('active_graph_version') or 0}; "
            f"documents: {search_state.get('document_count') or 0}; "
            f"cold p95: {(acceptance or {}).get('cold_p95_ms', '-') } ms; "
            f"warm p95: {(acceptance or {}).get('warm_p95_ms', '-') } ms."
        )
    else:
        lines.append(
            "The safest next move is to inspect pending/high-severity queue items, refresh policy after decisions, "
            "validate the trusted candidate graph, refresh candidate search, run the benchmark, then run publish dry-run."
        )

    lines.extend(
        _next_step_lines(
            selected_item=selected_item,
            hard_blockers=hard_blockers,
            pending_count=pending_count,
            benchmark_status=benchmark_status,
            publish_status=publish_status,
        )
    )

    lines.append("I cannot approve, repair, or publish by chat; I can explain evidence and point you to the next safe action.")
    return "\n\n".join(lines)


@router.post("/exports/{export_id}/assistant/chat")
def governance_assistant_chat(export_id: str, payload: GovernanceAssistantRequest):
    engine = migration_engine()
    try:
        overview_payload = export_overview(engine, export_id)
        queue_payload = validation_queue(engine, export_id, status=None, issue_type=None, limit=12, offset=0)
        activity_payload = activity(engine, export_id, limit=12)
        schema_payload = schema_summary(engine, export_id)
        provenance_events = provenance(engine, export_id, subject=payload.subject, limit=12)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Export not found: {export_id}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Governance evidence is unavailable: {exc}") from exc

    citations = _assistant_citations(export_id, overview_payload, queue_payload, activity_payload, provenance_events)
    mode = "deterministic"
    llm_available, llm_reason = llm_config_status()
    answer = _deterministic_governance_answer(
        export_id=export_id,
        message=payload.message,
        screen=payload.screen,
        selected_item=payload.selected_item,
        overview_payload=overview_payload,
        queue_payload=queue_payload,
        activity_payload=activity_payload,
        schema_payload=schema_payload,
    )
    if payload.use_llm and llm_available:
        context = {
            "export_id": export_id,
            "current_screen": payload.screen,
            "selected_item": payload.selected_item,
            "overview": overview_payload,
            "queue_sample": queue_payload.get("items", [])[:8],
            "activity_sample": {
                "agent_runs": activity_payload.get("agent_runs", [])[:4],
                "tool_executions": activity_payload.get("tool_executions", [])[:4],
                "approvals": activity_payload.get("approvals", [])[:4],
            },
            "schema_summary": {
                "table_count": len(schema_payload.get("tables") or []),
                "mapping_proposal_count": len(schema_payload.get("mapping_proposals") or []),
                "mapping_proposals": schema_payload.get("mapping_proposals", [])[:5],
            },
            "provenance": provenance_events[:8],
        }
        history = "\n".join(f"{item.role}: {item.content}" for item in payload.history[-6:])
        system_prompt = (
            "You are the Migration Governance Assistant for non-technical enterprise users. "
            "Explain migration governance in plain language using only the provided evidence. "
            "Be concise, concrete, and calming. Do not claim you approved, repaired, published, "
            "or changed data. If the evidence is insufficient, say what should be checked next. "
            "Use the terms trusted, quarantine, candidate graph, dry-run, and publish only with a short explanation. "
            "Return strict JSON only with this shape: {\"answer\": \"plain language answer\"}."
        )
        user_prompt = (
            f"Conversation history:\n{history or 'None'}\n\n"
            f"User question:\n{payload.message}\n\n"
            f"Evidence JSON:\n{compact_json(context, max_chars=16000)}"
        )
        try:
            raw_answer, model_name = call_chat_llm(system_prompt, user_prompt)
            answer = str(parse_json_object(raw_answer).get("answer") or raw_answer)
            mode = f"llm:{model_name}"
        except Exception as exc:  # noqa: BLE001
            mode = f"deterministic_after_llm_error:{exc}"
    elif payload.use_llm and not llm_available:
        mode = f"deterministic_no_llm:{llm_reason}"

    return {
        "status": "ok",
        "mode": mode,
        "answer": answer,
        "citations": citations,
        "suggested_questions": [
            "What should I do next before publish?",
            "Explain trusted versus quarantine in simple terms.",
            "Why is this item blocking publication?",
            "What happens after I accept a queue decision?",
            "Is the candidate graph ready for users?",
        ],
        "guardrails": [
            "read_only_assistant",
            "no_queue_decisions_from_chat",
            "no_publish_from_chat",
            "evidence_required",
        ],
    }


@router.get("/exports/{export_id}/provenance")
def migration_provenance(
    export_id: str,
    subject: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    return {"items": provenance(migration_engine(), export_id, subject=subject, limit=limit)}


@router.get("/exports/{export_id}/reports/{report_name}")
def migration_report(export_id: str, report_name: str):
    allowed = {
        "conditional-publish": "conditional_publish_report.json",
        "benchmark": "fast_search_benchmark_report.json",
        "publish": "publish_report.json",
        "graph-build": "graph_build_report.json",
        "schema-kg": "schema_intelligence_kg_report.json",
    }
    filename = allowed.get(report_name)
    if not filename:
        raise HTTPException(status_code=404, detail="Unknown report.")
    report = load_report(export_id, filename)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report not generated: {report_name}")
    return report


@router.get("/exports/{export_id}/candidate-search")
def candidate_search(
    export_id: str,
    response: Response,
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    started = perf_counter()
    normalized_query = q.strip().lower()
    for key, cached in _candidate_search_cache.items():
        if key[0] == export_id and key[1] == normalized_query and key[2] == limit:
            response.headers["X-Cache"] = "HIT"
            response.headers["X-Graph-Version"] = key[3]
            response.headers["Server-Timing"] = "candidate-search;dur=0.1"
            return cached
    engine = migration_engine()
    with engine.connect() as conn:
        state = conn.execute(text("""
            SELECT active_graph_version, document_count
            FROM lineage_search_state WHERE singleton = true
        """)).mappings().first()
        if not state or int(state["active_graph_version"] or 0) <= 0 or int(state["document_count"] or 0) <= 0:
            raise HTTPException(status_code=409, detail="Refresh the isolated candidate search index before benchmarking.")
        version = str(state["active_graph_version"] if state else "legacy")
        cache_key = (export_id, normalized_query, limit, version)
        exact = conn.execute(text("""
            SELECT node_id, label, technical_name, entity_level AS type,
                   path_full, parent_node_id, 1000::double precision AS score
            FROM lineage_search_document
            WHERE node_id = :query
            LIMIT :limit
        """), {"query": q, "limit": limit}).mappings().all()
        rows = exact or conn.execute(text("""
            WITH query AS (
                SELECT lineage_search_normalize(:query) AS normalized
            ), candidates AS (
                SELECT document.*
                FROM lineage_search_document document CROSS JOIN query
                WHERE document.search_text ILIKE '%' || query.normalized || '%'
                LIMIT 500
            )
            SELECT candidates.node_id,
                   candidates.label,
                   candidates.technical_name,
                   candidates.entity_level AS type,
                   candidates.path_full,
                   candidates.parent_node_id,
                   CASE
                       WHEN candidates.node_id = :query THEN 1000
                       WHEN candidates.normalized_technical_name = query.normalized THEN 900
                       WHEN candidates.normalized_label = query.normalized THEN 850
                       WHEN candidates.normalized_path = query.normalized THEN 800
                       ELSE greatest(
                           similarity(candidates.normalized_label, query.normalized),
                           similarity(candidates.normalized_technical_name, query.normalized),
                           similarity(candidates.normalized_path, query.normalized)
                       ) * 100
                   END AS score
            FROM candidates CROSS JOIN query
            ORDER BY score DESC, candidates.label NULLS LAST, candidates.node_id
            LIMIT :limit
        """), {"query": q, "limit": limit}).mappings().all()
    elapsed_ms = (perf_counter() - started) * 1000
    payload = {"query": q, "count": len(rows), "results": [dict(row) for row in rows]}
    _candidate_search_cache[cache_key] = payload
    response.headers["X-Cache"] = "MISS"
    response.headers["X-Graph-Version"] = version
    response.headers["Server-Timing"] = f"candidate-search;dur={elapsed_ms:.2f}"
    return payload


@router.post("/exports/{export_id}/actions/{action}")
def run_governance_action(export_id: str, action: str, payload: GovernanceActionRequest):
    action_specs = {
        "refresh-policy": (
            "ValidationAgent", "build_conditional_projection",
            lambda settings: {"export_id": export_id, "env_config": str(resolve_path(settings.migration_v2_env_config_path))},
        ),
        "candidate-dry-run": (
            "GraphBuildAgent", "build_candidate_graph",
            lambda settings: {"export_id": export_id, "env_config": str(resolve_path(settings.migration_v2_env_config_path)), "dry_run": True},
        ),
        "resolve-structural-parity": (
            "ValidationAgent", "resolve_structural_parity",
            lambda settings: {
                "export_id": export_id,
                "env_config": str(resolve_path(settings.migration_v2_env_config_path)),
                "apply": True,
                "approved_by": payload.requested_by,
            },
        ),
        "enforce-trusted-graph": (
            "GraphBuildAgent", "enforce_trusted_graph_projection",
            lambda settings: {
                "export_id": export_id,
                "env_config": str(resolve_path(settings.migration_v2_env_config_path)),
            },
        ),
        "benchmark": (
            "PublishGuardianAgent", "run_search_benchmark",
            lambda settings: {
                "export_id": export_id,
                "env_config": str(resolve_path(settings.migration_v2_env_config_path)),
                "api_base_url": payload.api_base_url,
                "search_path": f"/migration-v2/exports/{export_id}/candidate-search",
            },
        ),
        "activate-candidate-search": (
            "PublishGuardianAgent", "activate_candidate_search",
            lambda settings: {"export_id": export_id, "env_config": str(resolve_path(settings.migration_v2_env_config_path))},
        ),
        "evaluate-agent": (
            "ValidationGuardianAgent", "evaluate_validation_agent",
            lambda settings: {
                "export_id": export_id,
                "env_config": str(resolve_path(settings.migration_v2_env_config_path)),
                "limit": 100,
                "mode": "deterministic",
                "bootstrap_from_queue": True,
            },
        ),
        "publish-dry-run": (
            "PublishGuardianAgent", "publish_graph_version",
            lambda settings: {"export_id": export_id, "env_config": str(resolve_path(settings.migration_v2_env_config_path)), "dry_run": True},
        ),
        "publish": (
            "PublishGuardianAgent", "publish_graph_version",
            lambda settings: {
                "export_id": export_id,
                "env_config": str(resolve_path(settings.migration_v2_env_config_path)),
                "dry_run": False,
                "approved_by": payload.requested_by,
            },
        ),
    }
    if action not in action_specs:
        raise HTTPException(status_code=404, detail="Unknown governance action.")
    engine = migration_engine()
    repository = WorkflowRepository(engine)
    with engine.connect() as conn:
        run_id = conn.execute(text("""
            SELECT run_id::text FROM migration_workflow_run
            WHERE export_id = :export_id ORDER BY updated_at DESC LIMIT 1
        """), {"export_id": export_id}).scalar()
    if not run_id:
        raise HTTPException(status_code=409, detail="Start a workflow before running governance actions.")
    state = repository.get_run(str(run_id))
    settings = get_settings()
    agent_name, tool_name, payload_builder = action_specs[action]
    runtime = AllowlistedToolRuntime(repository, state, migration_postgres_url())
    try:
        result = runtime.execute(
            agent_name=agent_name,
            tool_name=tool_name,
            payload=payload_builder(settings),
            refresh=True,
        )
        if action == "activate-candidate-search":
            _candidate_search_cache.clear()
        return {"action": action, "requested_by": payload.requested_by, "result": result}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=f"Governance action failed: {exc}") from exc
