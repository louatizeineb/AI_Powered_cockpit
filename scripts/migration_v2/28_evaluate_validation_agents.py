from __future__ import annotations

import argparse
import csv
import json
import os
import random
from collections import defaultdict
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import text

from _common import (
    REPORT_ROOT,
    ROOT,
    config_section,
    ensure_tables,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
    write_json_report,
    write_markdown_report,
)
from app.migration_v2.agents.base import (
    ALLOWED_POLICIES,
    AgentProposal,
    call_chat_llm,
    enforce_guardrails,
    llm_config_status,
    llm_settings,
    parse_json_object,
    proposal_from_payload,
)
from app.migration_v2.agents.evidence_retriever import retrieve_evidence_packet
from app.migration_v2.agents.reasoning import build_evidence_plan, evidence_plan_to_dict
from app.migration_v2.agents.validation_guardian_agent import (
    SYSTEM_PROMPT,
    deterministic_proposal,
    normalize_item,
    user_prompt_for_item,
)


LOGGER = setup_logging("migration_v2.evaluate_validation_agents")
EVAL_SQL = ROOT / "backend" / "migrations" / "sql" / "020_migration_v2_agent_evaluations.sql"
AGENT_NAME = "ValidationGuardianAgent"
BLOCKER_POLICIES = {"repair", "block"}
SOFT_POLICIES = {"quarantine", "needs_human"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate migration_v2 validation queue agent proposals.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum eval cases.")
    parser.add_argument("--issue-type", help="Optional issue_type filter.")
    parser.add_argument(
        "--sample-strategy",
        choices=["ordered", "random", "stratified"],
        default="ordered",
        help="How to select eval cases after bootstrapping.",
    )
    parser.add_argument("--per-bucket", type=int, default=4, help="Cases per issue/policy bucket for stratified sampling.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic random seed for random/stratified sampling.")
    parser.add_argument(
        "--mode",
        choices=["latest_proposals", "deterministic", "llm_live"],
        default="latest_proposals",
        help="Evaluate latest persisted proposals, deterministic fallback, or live LLM proposals.",
    )
    parser.add_argument("--require-llm", action="store_true", help="Fail llm_live mode instead of falling back.")
    parser.add_argument(
        "--bootstrap-from-queue",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Seed active eval cases from approved/resolved validation queue decisions.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write eval database rows.")
    return parser.parse_args()


def engine_from_args(args: argparse.Namespace):
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
        if v2_config.get("postgres_url"):
            return postgres_engine_from_url(v2_config["postgres_url"])
    return postgres_engine()


def apply_eval_schema(engine) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(EVAL_SQL.read_text(encoding="utf-8"))


def bootstrap_cases(engine, export_id: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO migration_agent_eval_case (
                    export_id, case_id, issue_id, issue_type, expected_policy,
                    expected_status, severity, evidence_snapshot, source, notes, active, updated_at
                )
                SELECT export_id,
                       'queue:' || issue_id AS case_id,
                       issue_id,
                       issue_type,
                       publish_policy AS expected_policy,
                       queue_status AS expected_status,
                       severity,
                       jsonb_build_object(
                           'queue', jsonb_build_object(
                               'issue_id', issue_id,
                               'issue_type', issue_type,
                               'entity_kind', entity_kind,
                               'node_id', node_id,
                               'src_node_id', src_node_id,
                               'tgt_node_id', tgt_node_id,
                               'relationship_type', relationship_type,
                               'severity', severity,
                               'confidence', confidence,
                               'publish_policy', publish_policy,
                               'queue_status', queue_status,
                               'proposed_action', proposed_action,
                               'rationale', rationale,
                               'evidence', evidence
                           )
                       ) AS evidence_snapshot,
                       'approved_validation_queue' AS source,
                       coalesce(rationale, '') AS notes,
                       true AS active,
                       now() AS updated_at
                FROM migration_validation_queue
                WHERE export_id = :export_id
                  AND queue_status IN ('approved', 'resolved')
                  AND publish_policy IN ('accept', 'quarantine', 'exclude', 'repair', 'needs_human', 'block')
                ON CONFLICT (export_id, case_id)
                DO UPDATE SET
                    issue_type = EXCLUDED.issue_type,
                    expected_policy = EXCLUDED.expected_policy,
                    expected_status = EXCLUDED.expected_status,
                    severity = EXCLUDED.severity,
                    evidence_snapshot = EXCLUDED.evidence_snapshot,
                    source = EXCLUDED.source,
                    notes = EXCLUDED.notes,
                    active = true,
                    updated_at = now()
                """
            ),
            {"export_id": export_id},
        )
    return int(result.rowcount or 0)


def fetch_cases(
    engine,
    export_id: str,
    *,
    limit: int,
    issue_type: str | None,
    sample_strategy: str = "ordered",
    per_bucket: int = 4,
    seed: int = 42,
) -> list[dict[str, Any]]:
    filters = ["eval.export_id = :export_id", "eval.active = true"]
    params: dict[str, Any] = {"export_id": export_id, "limit": limit}
    if issue_type:
        filters.append("eval.issue_type = :issue_type")
        params["issue_type"] = issue_type
    fetch_limit = limit if sample_strategy == "ordered" else max(limit * 10, 500)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT eval.id, eval.case_id, eval.issue_id, eval.issue_type,
                       eval.expected_policy, eval.expected_status, eval.severity,
                       eval.evidence_snapshot, eval.source, eval.notes,
                       queue.entity_kind, queue.node_id, queue.src_node_id, queue.tgt_node_id,
                       queue.relationship_type, queue.confidence, queue.publish_policy,
                       queue.queue_status, queue.source_report, queue.source_decision_status,
                       queue.proposed_action, queue.rationale, queue.evidence
                FROM migration_agent_eval_case eval
                LEFT JOIN migration_validation_queue queue
                  ON queue.export_id = eval.export_id AND queue.issue_id = eval.issue_id
                WHERE {' AND '.join(filters)}
                ORDER BY eval.updated_at DESC, eval.issue_type, eval.issue_id
                LIMIT :fetch_limit
                """
            ),
            {**params, "fetch_limit": fetch_limit},
        ).mappings().all()
    cases = [dict(row) for row in rows]
    if sample_strategy == "ordered":
        return cases[:limit]
    rng = random.Random(seed)
    if sample_strategy == "random":
        rng.shuffle(cases)
        return cases[:limit]
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        buckets[(
            str(case.get("issue_type") or ""),
            str(case.get("relationship_type") or ""),
            str(case.get("expected_policy") or ""),
        )].append(case)
    for bucket_cases in buckets.values():
        rng.shuffle(bucket_cases)
    selected: list[dict[str, Any]] = []
    for key in sorted(buckets):
        selected.extend(buckets[key][: max(1, per_bucket)])
        if len(selected) >= limit:
            return selected[:limit]
    remaining = [
        case
        for key in sorted(buckets)
        for case in buckets[key][max(1, per_bucket):]
    ]
    rng.shuffle(remaining)
    selected.extend(remaining)
    return selected[:limit]


def latest_proposals(engine, export_id: str, issue_ids: list[str]) -> dict[str, AgentProposal]:
    if not issue_ids:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT ON (issue_id)
                       issue_id, issue_type, proposed_policy, confidence, rationale,
                       missing_evidence, human_question, guardrail_actions, fallback_used,
                       raw_model_response
                FROM migration_agent_proposal
                WHERE export_id = :export_id
                  AND issue_id = ANY(:issue_ids)
                ORDER BY issue_id, created_at DESC
                """
            ),
            {"export_id": export_id, "issue_ids": issue_ids},
        ).mappings().all()
    proposals: dict[str, AgentProposal] = {}
    for row in rows:
        proposals[str(row["issue_id"])] = AgentProposal(
            issue_id=str(row["issue_id"]),
            issue_type=row.get("issue_type"),
            proposed_policy=str(row.get("proposed_policy") or "needs_human"),
            confidence=float(row.get("confidence") or 0),
            rationale=str(row.get("rationale") or ""),
            missing_evidence=[str(item) for item in (row.get("missing_evidence") or [])],
            human_question=str(row.get("human_question") or ""),
            guardrail_actions=[str(item) for item in (row.get("guardrail_actions") or [])],
            raw_model_response=str(row.get("raw_model_response") or ""),
            fallback_used=bool(row.get("fallback_used")),
        )
    return proposals


def live_llm_proposal(item: dict[str, Any]) -> tuple[AgentProposal, str]:
    raw_response, model_name = call_chat_llm(SYSTEM_PROMPT, user_prompt_for_item(item))
    payload = parse_json_object(raw_response)
    payload.setdefault("issue_id", item["issue_id"])
    payload.setdefault("issue_type", item.get("issue_type"))
    proposal = proposal_from_payload(payload, raw_model_response=raw_response)
    return enforce_guardrails(proposal, item), model_name


def queue_item_from_case(case: dict[str, Any]) -> dict[str, Any]:
    snapshot = case.get("evidence_snapshot") or {}
    queue_snapshot = (snapshot.get("queue") if isinstance(snapshot, dict) else None) or {}
    item = {
        "issue_id": case.get("issue_id"),
        "issue_type": case.get("issue_type"),
        "entity_kind": case.get("entity_kind") or queue_snapshot.get("entity_kind"),
        "node_id": case.get("node_id") or queue_snapshot.get("node_id"),
        "src_node_id": case.get("src_node_id") or queue_snapshot.get("src_node_id"),
        "tgt_node_id": case.get("tgt_node_id") or queue_snapshot.get("tgt_node_id"),
        "relationship_type": case.get("relationship_type") or queue_snapshot.get("relationship_type"),
        "severity": case.get("severity") or queue_snapshot.get("severity"),
        "confidence": case.get("confidence") or queue_snapshot.get("confidence"),
        "publish_policy": case.get("publish_policy") or queue_snapshot.get("publish_policy"),
        "queue_status": case.get("queue_status") or queue_snapshot.get("queue_status"),
        "source_report": case.get("source_report") or queue_snapshot.get("source_report"),
        "source_decision_status": case.get("source_decision_status") or queue_snapshot.get("source_decision_status"),
        "proposed_action": case.get("proposed_action") or queue_snapshot.get("proposed_action"),
        "rationale": case.get("rationale") or queue_snapshot.get("rationale"),
        "evidence": case.get("evidence") or queue_snapshot.get("evidence") or {},
    }
    return normalize_item(item)


def evidence_grounded_score(proposal: AgentProposal, case: dict[str, Any], item: dict[str, Any]) -> float:
    evidence = item.get("evidence") or {}
    retrieved = item.get("retrieved_evidence") or {}
    counts = retrieved.get("counts") or {}
    text_value = " ".join(
        [
            proposal.rationale.lower(),
            proposal.human_question.lower(),
            " ".join(item.lower() for item in proposal.missing_evidence),
        ]
    )
    signals = [
        str(case.get("issue_type") or "").lower(),
        str(case.get("relationship_type") or "").lower(),
        *[str(value).lower() for value in (evidence.get("conflict_fields") or [])[:5]],
        *[str(value).lower() for value in (evidence.get("observed_roles") or [])[:5]],
        *[str(value).lower() for value in (evidence.get("source_tables") or [])[:5]],
    ]
    if counts:
        category_terms = {
            "object_rows": "object row",
            "relationship_neighbors": "relationship neighbor",
            "lineage_examples": "lineage example",
            "similar_decisions": "similar approved decision",
            "schema_columns": "schema column",
            "provenance_events": "provenance event",
        }
        signals.extend([category_terms.get(key, key.replace("_", " ")) for key, value in counts.items() if int(value or 0) > 0])
    signals = [signal for signal in signals if signal and signal != "none"]
    if not signals:
        return 0.5 if proposal.rationale else 0.0
    matches = sum(1 for signal in signals if signal in text_value)
    return min(1.0, matches / max(3, len(signals)))


def evidence_plan_scores(case: dict[str, Any], item: dict[str, Any]) -> tuple[bool, float, float, dict[str, Any]]:
    plan = item.get("evidence_plan") or build_evidence_plan(item)
    payload = evidence_plan_to_dict(plan)
    issue_type = str(case.get("issue_type") or item.get("issue_type") or "")
    relationship_type = str(case.get("relationship_type") or item.get("relationship_type") or "")
    required = " ".join(str(value).lower() for value in payload.get("required_evidence") or [])
    objective = str(payload.get("objective") or "").lower()
    repair = str(payload.get("repair_strategy") or "").lower()
    queries = payload.get("planned_queries") or []
    risks = payload.get("risk_flags") or []
    present = bool(payload.get("objective") and payload.get("required_evidence") and queries)

    expected_terms = {
        "duplicate_role_path_conflict": ["duplicate", "role", "path", "conflict"],
        "placeholder_path_missing_parent_metadata": ["parent", "metadata", "placeholder"],
        "pathful_leaf_missing_parent_metadata": ["parent", "lineage", "path"],
    }.get(issue_type, [])
    if relationship_type:
        expected_terms.extend([relationship_type.lower(), "edge" if relationship_type in {"HAS_FIELD", "IMPLEMENTS"} else "relationship"])

    term_text = " ".join([required, objective, repair])
    term_score = (
        sum(1 for term in expected_terms if term and term.lower() in term_text) / max(1, len(expected_terms))
        if expected_terms
        else 0.5
    )
    query_score = 0.0
    if queries:
        read_only = sum(
            1
            for query in queries
            if str(query.get("safety") or "") == "read_only_select"
            and str(query.get("sql") or "").lstrip().lower().startswith("select")
        )
        query_score = read_only / len(queries)
    risk_score = 1.0 if risks else 0.5
    plan_score = round((0.35 * float(present)) + (0.35 * term_score) + (0.2 * query_score) + (0.1 * risk_score), 4)
    return present, min(1.0, plan_score), round(query_score, 4), payload


def score_case(case: dict[str, Any], proposal: AgentProposal, source: str, item: dict[str, Any], latency_ms: float = 0.0) -> dict[str, Any]:
    expected = str(case["expected_policy"])
    proposed = str(proposal.proposed_policy or "needs_human")
    blocker_expected = expected in BLOCKER_POLICIES
    valid_policy = proposed in ALLOWED_POLICIES
    policy_exact = expected == proposed
    unsafe_accept = proposed == "accept" and expected not in {"accept", "quarantine"}
    blocker_recalled = (not blocker_expected) or proposed in BLOCKER_POLICIES
    plan_present, plan_score, query_score, plan_payload = evidence_plan_scores(case, item)
    return {
        "case_id": case["case_id"],
        "issue_id": case["issue_id"],
        "issue_type": case.get("issue_type"),
        "expected_policy": expected,
        "proposed_policy": proposed,
        "confidence": max(0.0, min(1.0, float(proposal.confidence or 0))),
        "policy_exact": policy_exact,
        "unsafe_accept": unsafe_accept,
        "blocker_expected": blocker_expected,
        "blocker_recalled": blocker_recalled,
        "valid_policy": valid_policy,
        "rationale_present": bool(proposal.rationale.strip()),
        "question_present": bool(proposal.human_question.strip()),
        "evidence_grounded_score": evidence_grounded_score(proposal, case, item),
        "evidence_plan_present": plan_present,
        "evidence_plan_score": plan_score,
        "query_intent_score": query_score,
        "latency_ms": latency_ms,
        "rationale": proposal.rationale,
        "human_question": proposal.human_question,
        "missing_evidence": proposal.missing_evidence,
        "guardrail_actions": proposal.guardrail_actions,
        "source": source,
        "details": {
            "severity": case.get("severity"),
            "expected_status": case.get("expected_status"),
            "case_source": case.get("source"),
            "fallback_used": proposal.fallback_used,
            "retrieved_evidence_counts": (item.get("retrieved_evidence") or {}).get("counts") or {},
            "evidence_plan": plan_payload,
        },
    }


def summarize(
    scores: list[dict[str, Any]],
    *,
    bootstrapped_count: int,
    mode: str,
    errors: list[str],
    llm_call_count: int = 0,
    fallback_count: int = 0,
    model_name: str | None = None,
) -> dict[str, Any]:
    total = len(scores)
    blocker_expected_count = sum(1 for item in scores if item["blocker_expected"])
    def rate(key: str) -> float:
        return round(sum(1 for item in scores if item[key]) / total, 4) if total else 0.0
    average_grounded = round(sum(item["evidence_grounded_score"] for item in scores) / total, 4) if total else 0.0
    average_plan = round(sum(item["evidence_plan_score"] for item in scores) / total, 4) if total else 0.0
    average_query = round(sum(item["query_intent_score"] for item in scores) / total, 4) if total else 0.0
    policy_accuracy = rate("policy_exact")
    status = (
        "ready"
        if total
        and policy_accuracy >= 0.9
        and average_grounded >= 0.35
        and average_plan >= 0.75
        and average_query >= 0.95
        and not any(item["unsafe_accept"] for item in scores)
        else "needs_attention"
    )
    if not total:
        status = "no_cases"
    return {
        "status": status,
        "mode": mode,
        "model_name": model_name,
        "case_count": total,
        "bootstrapped_count": bootstrapped_count,
        "llm_call_count": llm_call_count,
        "fallback_count": fallback_count,
        "policy_accuracy": policy_accuracy,
        "unsafe_accept_count": sum(1 for item in scores if item["unsafe_accept"]),
        "unsafe_accept_rate": rate("unsafe_accept"),
        "blocker_expected_count": blocker_expected_count,
        "blocker_recall": (
            round(sum(1 for item in scores if item["blocker_expected"] and item["blocker_recalled"]) / blocker_expected_count, 4)
            if blocker_expected_count else 1.0
        ),
        "valid_policy_rate": rate("valid_policy"),
        "question_present_rate": rate("question_present"),
        "rationale_present_rate": rate("rationale_present"),
        "average_confidence": round(sum(item["confidence"] for item in scores) / total, 4) if total else 0.0,
        "average_evidence_grounded_score": average_grounded,
        "average_evidence_plan_score": average_plan,
        "average_query_intent_score": average_query,
        "errors": errors,
    }


def insert_eval_run(engine, export_id: str, summary: dict[str, Any], latency_ms: float) -> int:
    with engine.begin() as conn:
        run_id = conn.execute(
            text(
                """
                INSERT INTO migration_agent_eval_run (
                    export_id, agent_name, eval_name, mode, status, case_count,
                    policy_accuracy, unsafe_accept_count, blocker_recall, valid_policy_rate,
                    question_present_rate, rationale_present_rate, average_confidence,
                    average_evidence_plan_score, average_query_intent_score,
                    latency_ms, langsmith_project, summary, errors, completed_at
                )
                VALUES (
                    :export_id, :agent_name, :eval_name, :mode, :status, :case_count,
                    :policy_accuracy, :unsafe_accept_count, :blocker_recall, :valid_policy_rate,
                    :question_present_rate, :rationale_present_rate, :average_confidence,
                    :average_evidence_plan_score, :average_query_intent_score,
                    :latency_ms, :langsmith_project, CAST(:summary AS jsonb), CAST(:errors AS jsonb), now()
                )
                RETURNING id
                """
            ),
            {
                "export_id": export_id,
                "agent_name": AGENT_NAME,
                "eval_name": "validation_queue_policy_eval",
                "mode": summary["mode"],
                "status": summary["status"],
                "case_count": summary["case_count"],
                "policy_accuracy": summary["policy_accuracy"],
                "unsafe_accept_count": summary["unsafe_accept_count"],
                "blocker_recall": summary["blocker_recall"],
                "valid_policy_rate": summary["valid_policy_rate"],
                "question_present_rate": summary["question_present_rate"],
                "rationale_present_rate": summary["rationale_present_rate"],
                "average_confidence": summary["average_confidence"],
                "average_evidence_plan_score": summary["average_evidence_plan_score"],
                "average_query_intent_score": summary["average_query_intent_score"],
                "latency_ms": latency_ms,
                "langsmith_project": os.getenv("LANGSMITH_PROJECT"),
                "summary": json.dumps(summary, ensure_ascii=False),
                "errors": json.dumps(summary.get("errors") or [], ensure_ascii=False),
            },
        ).scalar_one()
    return int(run_id)


def insert_scores(engine, export_id: str, eval_run_id: int, scores: list[dict[str, Any]]) -> None:
    if not scores:
        return
    rows = []
    for score in scores:
        rows.append({
            "eval_run_id": eval_run_id,
            "export_id": export_id,
            "case_id": score["case_id"],
            "issue_id": score["issue_id"],
            "issue_type": score.get("issue_type"),
            "expected_policy": score["expected_policy"],
            "proposed_policy": score["proposed_policy"],
            "confidence": score["confidence"],
            "policy_exact": score["policy_exact"],
            "unsafe_accept": score["unsafe_accept"],
            "blocker_expected": score["blocker_expected"],
            "blocker_recalled": score["blocker_recalled"],
            "valid_policy": score["valid_policy"],
            "rationale_present": score["rationale_present"],
            "question_present": score["question_present"],
            "evidence_grounded_score": score["evidence_grounded_score"],
            "evidence_plan_present": score["evidence_plan_present"],
            "evidence_plan_score": score["evidence_plan_score"],
            "query_intent_score": score["query_intent_score"],
            "latency_ms": score["latency_ms"],
            "rationale": score["rationale"],
            "human_question": score["human_question"],
            "missing_evidence": json.dumps(score["missing_evidence"], ensure_ascii=False),
            "guardrail_actions": json.dumps(score["guardrail_actions"], ensure_ascii=False),
            "source": score["source"],
            "details": json.dumps(score["details"], ensure_ascii=False),
        })
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO migration_agent_eval_score (
                    eval_run_id, export_id, case_id, issue_id, issue_type,
                    expected_policy, proposed_policy, confidence, policy_exact,
                    unsafe_accept, blocker_expected, blocker_recalled, valid_policy,
                    rationale_present, question_present, evidence_grounded_score,
                    evidence_plan_present, evidence_plan_score, query_intent_score,
                    latency_ms, rationale, human_question, missing_evidence,
                    guardrail_actions, source, details
                )
                VALUES (
                    :eval_run_id, :export_id, :case_id, :issue_id, :issue_type,
                    :expected_policy, :proposed_policy, :confidence, :policy_exact,
                    :unsafe_accept, :blocker_expected, :blocker_recalled, :valid_policy,
                    :rationale_present, :question_present, :evidence_grounded_score,
                    :evidence_plan_present, :evidence_plan_score, :query_intent_score,
                    :latency_ms, :rationale, :human_question, CAST(:missing_evidence AS jsonb),
                    CAST(:guardrail_actions AS jsonb), :source, CAST(:details AS jsonb)
                )
                """
            ),
            rows,
        )


def write_eval_reports(export_id: str, eval_run_id: int | None, summary: dict[str, Any], scores: list[dict[str, Any]]) -> tuple[Path, Path, Path]:
    payload = {"export_id": export_id, "eval_run_id": eval_run_id, "summary": summary, "scores": scores}
    json_path = write_json_report(export_id, "agent_evaluation_report.json", payload)
    md_path = write_markdown_report(
        export_id,
        "agent_evaluation_report.md",
        "Migration V2 Agent Evaluation Report",
        [
            ("Status", f"`{summary['status']}`"),
            (
                "Metrics",
                "\n".join(
                    [
                        f"- `mode`: `{summary['mode']}`",
                        f"- `model_name`: `{summary.get('model_name') or '-'}`",
                        f"- `case_count`: {summary['case_count']}",
                        f"- `llm_call_count`: {summary.get('llm_call_count', 0)}",
                        f"- `fallback_count`: {summary.get('fallback_count', 0)}",
                        f"- `policy_accuracy`: {summary['policy_accuracy']:.2%}",
                        f"- `unsafe_accept_count`: {summary['unsafe_accept_count']}",
                        f"- `blocker_recall`: {summary['blocker_recall']:.2%}",
                        f"- `valid_policy_rate`: {summary['valid_policy_rate']:.2%}",
                        f"- `question_present_rate`: {summary['question_present_rate']:.2%}",
                        f"- `average_evidence_grounded_score`: {summary['average_evidence_grounded_score']:.2%}",
                        f"- `average_evidence_plan_score`: {summary['average_evidence_plan_score']:.2%}",
                        f"- `average_query_intent_score`: {summary['average_query_intent_score']:.2%}",
                    ]
                ),
            ),
            (
                "Failures",
                "\n".join(
                    f"- `{score['issue_id']}` expected `{score['expected_policy']}` got `{score['proposed_policy']}`"
                    for score in scores
                    if not score["policy_exact"]
                )
                or "No policy mismatches.",
            ),
            ("Unsafe Accepts", "\n".join(f"- `{score['issue_id']}`" for score in scores if score["unsafe_accept"]) or "None."),
        ],
    )
    csv_path = REPORT_ROOT / export_id / "manual_review_csv" / "11_agent_evaluation_scores.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "eval_run_id", "case_id", "issue_id", "issue_type", "expected_policy", "proposed_policy",
            "confidence", "policy_exact", "unsafe_accept", "blocker_expected", "blocker_recalled",
            "valid_policy", "question_present", "evidence_grounded_score", "source", "rationale", "human_question",
            "evidence_plan_present", "evidence_plan_score", "query_intent_score",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for score in scores:
            writer.writerow({**score, "eval_run_id": eval_run_id})
    return json_path, md_path, csv_path


def main() -> None:
    args = parse_args()
    engine = engine_from_args(args)
    ensure_tables(engine, ["migration_validation_queue"])
    apply_eval_schema(engine)
    started = perf_counter()
    bootstrapped = bootstrap_cases(engine, args.export_id) if args.bootstrap_from_queue else 0
    cases = fetch_cases(
        engine,
        args.export_id,
        limit=args.limit,
        issue_type=args.issue_type,
        sample_strategy=args.sample_strategy,
        per_bucket=args.per_bucket,
        seed=args.seed,
    )
    errors: list[str] = []
    scores: list[dict[str, Any]] = []
    settings = llm_settings()
    llm_available, llm_reason = llm_config_status(settings)
    llm_call_limit = max(0, min(args.limit, int(settings.llm_run_max_calls or 0)))
    llm_call_count = 0
    fallback_count = 0
    model_name: str | None = None
    if args.mode == "llm_live" and args.require_llm and not llm_available:
        raise RuntimeError(f"LLM is required, but chat config is unavailable: {llm_reason}.")
    proposals = latest_proposals(engine, args.export_id, [str(case["issue_id"]) for case in cases]) if args.mode == "latest_proposals" else {}

    for case in cases:
        case_started = perf_counter()
        source = args.mode
        item = queue_item_from_case(case)
        item["export_id"] = args.export_id
        item["evidence_plan"] = build_evidence_plan(item)
        item["retrieved_evidence"] = retrieve_evidence_packet(engine, args.export_id, item)
        proposal = proposals.get(str(case["issue_id"]))
        if args.mode == "llm_live":
            if llm_available and llm_call_count < llm_call_limit:
                try:
                    proposal, model_name = live_llm_proposal(item)
                    llm_call_count += 1
                    source = "llm_live"
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{case['issue_id']}: llm_failed: {exc}")
                    if args.require_llm:
                        raise
                    proposal = None
                    source = "deterministic_after_llm_error"
            elif llm_available:
                source = "deterministic_after_llm_budget_limit"
                proposal = None
            else:
                errors.append(f"{case['issue_id']}: llm_unavailable: {llm_reason}")
                if args.require_llm:
                    raise RuntimeError(f"LLM is required, but chat config is unavailable: {llm_reason}.")
                source = "deterministic_no_llm"
                proposal = None
        if proposal is None:
            if args.mode == "latest_proposals":
                source = "deterministic_no_persisted_proposal"
            fallback_count += 1
            proposal = enforce_guardrails(deterministic_proposal(item), item)
        scores.append(score_case(case, proposal, source, item, latency_ms=(perf_counter() - case_started) * 1000))

    summary = summarize(
        scores,
        bootstrapped_count=bootstrapped,
        mode=args.mode,
        errors=errors,
        llm_call_count=llm_call_count,
        fallback_count=fallback_count,
        model_name=model_name,
    )
    elapsed_ms = (perf_counter() - started) * 1000
    eval_run_id = None
    if not args.dry_run:
        eval_run_id = insert_eval_run(engine, args.export_id, summary, elapsed_ms)
        insert_scores(engine, args.export_id, eval_run_id, scores)
    json_path, md_path, csv_path = write_eval_reports(args.export_id, eval_run_id, summary, scores)
    LOGGER.info("Wrote %s, %s and %s", json_path, md_path, csv_path)
    LOGGER.info("Evaluation summary: %s", summary)


if __name__ == "__main__":
    main()
