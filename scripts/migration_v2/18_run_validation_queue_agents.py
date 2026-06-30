from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path
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
from app.migration_v2.agents.validation_guardian_agent import run as run_validation_queue_agent
from app.migration_v2.agents.reasoning import evidence_plan_to_dict


LOGGER = setup_logging("migration_v2.run_validation_queue_agents")

AGENT_SQL = ROOT / "backend" / "migrations" / "sql" / "013_migration_v2_agent_runs.sql"
AGENT_REASONING_SQL = ROOT / "backend" / "migrations" / "sql" / "021_migration_v2_agent_reasoning.sql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run migration_v2 validation queue agents.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    parser.add_argument("--agent", default="validation_queue", choices=["validation_queue"], help="Agent to run.")
    parser.add_argument("--limit", type=int, default=25, help="Maximum queue items to review.")
    parser.add_argument("--issue-type", help="Optional queue issue_type filter.")
    parser.add_argument("--require-llm", action="store_true", help="Fail instead of using deterministic fallback.")
    parser.add_argument("--dry-run", action="store_true", help="Run agent without writing database rows or reports.")
    return parser.parse_args()


def engine_from_args(args: argparse.Namespace):
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
        if v2_config.get("postgres_url"):
            return postgres_engine_from_url(v2_config["postgres_url"])
    return postgres_engine()


def apply_agent_schema(engine) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(AGENT_SQL.read_text(encoding="utf-8"))
        conn.exec_driver_sql(AGENT_REASONING_SQL.read_text(encoding="utf-8"))


def insert_agent_run(engine, result, requested_limit: int) -> int:
    with engine.begin() as conn:
        run_id = conn.execute(
            text(
                """
                INSERT INTO migration_agent_run (
                    export_id, agent_name, mode, model_name, status, requested_limit,
                    reviewed_count, proposal_count, llm_call_count, fallback_count,
                    errors, completed_at
                )
                VALUES (
                    :export_id, :agent_name, :mode, :model_name, :status, :requested_limit,
                    :reviewed_count, :proposal_count, :llm_call_count, :fallback_count,
                    CAST(:errors AS jsonb), now()
                )
                RETURNING id
                """
            ),
            {
                "export_id": result.export_id,
                "agent_name": result.agent_name,
                "mode": result.mode,
                "model_name": result.model_name,
                "status": result.status,
                "requested_limit": requested_limit,
                "reviewed_count": result.reviewed_count,
                "proposal_count": result.proposal_count,
                "llm_call_count": result.llm_call_count,
                "fallback_count": result.fallback_count,
                "errors": json.dumps(result.errors),
            },
        ).scalar_one()
    return int(run_id)


def insert_proposals(engine, result, run_id: int) -> None:
    rows = []
    for proposal in result.proposals:
        rows.append(
            {
                "export_id": result.export_id,
                "run_id": run_id,
                "agent_name": result.agent_name,
                "issue_id": proposal.issue_id,
                "issue_type": proposal.issue_type,
                "proposed_policy": proposal.proposed_policy,
                "confidence": proposal.confidence,
                "rationale": proposal.rationale,
                "missing_evidence": json.dumps(proposal.missing_evidence, ensure_ascii=False),
                "human_question": proposal.human_question,
                "guardrail_actions": json.dumps(proposal.guardrail_actions, ensure_ascii=False),
                "raw_model_response": proposal.raw_model_response,
                "fallback_used": proposal.fallback_used,
            }
        )
    if not rows:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO migration_agent_proposal (
                    export_id, run_id, agent_name, issue_id, issue_type, proposed_policy,
                    confidence, rationale, missing_evidence, human_question, guardrail_actions,
                    raw_model_response, fallback_used
                )
                VALUES (
                    :export_id, :run_id, :agent_name, :issue_id, :issue_type, :proposed_policy,
                    :confidence, :rationale, CAST(:missing_evidence AS jsonb), :human_question,
                    CAST(:guardrail_actions AS jsonb), :raw_model_response, :fallback_used
                )
                ON CONFLICT (export_id, run_id, issue_id)
                DO NOTHING
                """
            ),
            rows,
        )


def insert_evidence_plans(engine, result, run_id: int) -> None:
    rows = []
    for plan in result.evidence_plans:
        payload = evidence_plan_to_dict(plan)
        rows.append(
            {
                "export_id": result.export_id,
                "run_id": run_id,
                "agent_name": result.agent_name,
                "issue_id": payload["issue_id"],
                "issue_type": payload.get("issue_type"),
                "objective": payload["objective"],
                "required_evidence": json.dumps(payload["required_evidence"], ensure_ascii=False),
                "planned_queries": json.dumps(payload["planned_queries"], ensure_ascii=False),
                "planned_tools": json.dumps(payload["planned_tools"], ensure_ascii=False),
                "repair_strategy": payload["repair_strategy"],
                "risk_flags": json.dumps(payload["risk_flags"], ensure_ascii=False),
            }
        )
    if not rows:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO migration_agent_evidence_plan (
                    export_id, run_id, agent_name, issue_id, issue_type, objective,
                    required_evidence, planned_queries, planned_tools, repair_strategy, risk_flags
                )
                VALUES (
                    :export_id, :run_id, :agent_name, :issue_id, :issue_type, :objective,
                    CAST(:required_evidence AS jsonb), CAST(:planned_queries AS jsonb),
                    CAST(:planned_tools AS jsonb), :repair_strategy, CAST(:risk_flags AS jsonb)
                )
                ON CONFLICT (export_id, run_id, issue_id)
                DO NOTHING
                """
            ),
            rows,
        )


def proposal_payload(result, run_id: int | None) -> dict[str, Any]:
    plans_by_issue = {plan.issue_id: evidence_plan_to_dict(plan) for plan in result.evidence_plans}
    return {
        "export_id": result.export_id,
        "agent_name": result.agent_name,
        "run_id": run_id,
        "status": result.status,
        "mode": result.mode,
        "model_name": result.model_name,
        "reviewed_count": result.reviewed_count,
        "proposal_count": result.proposal_count,
        "llm_call_count": result.llm_call_count,
        "fallback_count": result.fallback_count,
        "errors": result.errors,
        "evidence_plan_count": len(result.evidence_plans),
        "evidence_plans": list(plans_by_issue.values()),
        "proposals": [
            {
                "issue_id": proposal.issue_id,
                "issue_type": proposal.issue_type,
                "proposed_policy": proposal.proposed_policy,
                "confidence": proposal.confidence,
                "rationale": proposal.rationale,
                "missing_evidence": proposal.missing_evidence,
                "human_question": proposal.human_question,
                "guardrail_actions": proposal.guardrail_actions,
                "fallback_used": proposal.fallback_used,
                "evidence_plan": plans_by_issue.get(proposal.issue_id),
            }
            for proposal in result.proposals
        ],
    }


def write_agent_reports(result, run_id: int | None) -> tuple[Path, Path, Path]:
    payload = proposal_payload(result, run_id)
    json_path = write_json_report(result.export_id, "agent_validation_queue_proposals.json", payload)
    md_path = write_markdown_report(
        result.export_id,
        "agent_validation_queue_proposals.md",
        "Migration V2 Agent Validation Queue Proposals",
        [
            ("Status", f"`{result.status}`"),
            (
                "Run",
                "\n".join(
                    [
                        f"- `run_id`: {run_id}",
                        f"- `mode`: `{result.mode}`",
                        f"- `model_name`: `{result.model_name}`",
                        f"- `reviewed_count`: {result.reviewed_count}",
                        f"- `proposal_count`: {result.proposal_count}",
                        f"- `evidence_plan_count`: {len(result.evidence_plans)}",
                        f"- `llm_call_count`: {result.llm_call_count}",
                        f"- `fallback_count`: {result.fallback_count}",
                    ]
                ),
            ),
            (
                "Evidence Plans",
                "\n".join(
                    f"- `{plan.issue_id}`: {len(plan.planned_queries)} planned read-only query(s), "
                    f"risks={', '.join(plan.risk_flags) or 'none'}"
                    for plan in result.evidence_plans[:50]
                )
                or "No evidence plans.",
            ),
            (
                "Proposal Summary",
                "\n".join(
                    f"- `{proposal.issue_id}`: `{proposal.proposed_policy}` confidence={proposal.confidence:.2f}"
                    for proposal in result.proposals[:50]
                )
                or "No proposals.",
            ),
            ("Errors", "\n".join(f"- {item}" for item in result.errors) or "None."),
        ],
    )
    csv_path = write_agent_csv(result, run_id)
    return json_path, md_path, csv_path


def write_agent_csv(result, run_id: int | None) -> Path:
    out_dir = REPORT_ROOT / result.export_id / "manual_review_csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    stable_path = out_dir / "10_agent_queue_proposals.csv"
    run_label = run_id if run_id is not None else "dry_run"
    run_path = out_dir / f"10_agent_queue_proposals_run_{run_label}.csv"
    columns = [
        "run_id",
        "agent_name",
        "issue_id",
        "issue_type",
        "agent_proposed_policy",
        "agent_confidence",
        "agent_rationale",
        "agent_missing_evidence",
        "agent_question",
        "agent_evidence_objective",
        "agent_planned_query_ids",
        "agent_repair_strategy",
        "agent_risk_flags",
        "guardrail_actions",
        "fallback_used",
        "reviewer_decision",
        "reviewer_notes",
    ]

    def write_csv(path: Path) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            plans_by_issue = {plan.issue_id: evidence_plan_to_dict(plan) for plan in result.evidence_plans}
            for proposal in result.proposals:
                plan = plans_by_issue.get(proposal.issue_id) or {}
                query_ids = [
                    str(query.get("query_id") or "")
                    for query in (plan.get("planned_queries") or [])
                    if query.get("query_id")
                ]
                writer.writerow(
                    {
                        "run_id": run_id,
                        "agent_name": result.agent_name,
                        "issue_id": proposal.issue_id,
                        "issue_type": proposal.issue_type,
                        "agent_proposed_policy": proposal.proposed_policy,
                        "agent_confidence": proposal.confidence,
                        "agent_rationale": proposal.rationale,
                        "agent_missing_evidence": " | ".join(proposal.missing_evidence),
                        "agent_question": proposal.human_question,
                        "agent_evidence_objective": plan.get("objective", ""),
                        "agent_planned_query_ids": " | ".join(query_ids),
                        "agent_repair_strategy": plan.get("repair_strategy", ""),
                        "agent_risk_flags": " | ".join(plan.get("risk_flags") or []),
                        "guardrail_actions": " | ".join(proposal.guardrail_actions),
                        "fallback_used": proposal.fallback_used,
                        "reviewer_decision": "",
                        "reviewer_notes": "",
                    }
                )

    write_csv(run_path)
    try:
        shutil.copyfile(run_path, stable_path)
        return stable_path
    except PermissionError:
        LOGGER.warning(
            "Could not update %s because it is locked. Wrote run-specific proposal CSV instead: %s",
            stable_path,
            run_path,
        )
        return run_path
    except OSError as exc:
        LOGGER.warning("Could not update %s: %s. Wrote %s instead.", stable_path, exc, run_path)
        return run_path


def main() -> None:
    args = parse_args()
    engine = engine_from_args(args)
    ensure_tables(engine, ["migration_validation_queue"])
    if not args.dry_run:
        apply_agent_schema(engine)

    result = run_validation_queue_agent(
        engine,
        args.export_id,
        limit=args.limit,
        issue_type=args.issue_type,
        require_llm=args.require_llm,
    )
    if args.require_llm and result.errors:
        raise SystemExit("LLM was required, but one or more LLM calls failed: " + "; ".join(result.errors[:3]))

    if args.dry_run:
        LOGGER.info(
            "Dry run reviewed %s items and produced %s proposals with mode=%s",
            result.reviewed_count,
            result.proposal_count,
            result.mode,
        )
        if result.errors:
            LOGGER.warning("Dry run errors: %s", result.errors)
        return

    run_id = insert_agent_run(engine, result, args.limit)
    insert_evidence_plans(engine, result, run_id)
    insert_proposals(engine, result, run_id)
    json_path, md_path, csv_path = write_agent_reports(result, run_id)
    LOGGER.info("Wrote %s, %s and %s", json_path, md_path, csv_path)


if __name__ == "__main__":
    main()
