from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from _common import REPORT_ROOT, ROOT, setup_logging, write_json_report, write_markdown_report
from app.migration_v2.agents.base import call_chat_llm, llm_config_status, parse_json_object


LOGGER = setup_logging("migration_v2.agent_publish_workflow")


@dataclass
class WorkflowStep:
    name: str
    command: list[str] = field(default_factory=list)
    status: str = "pending"
    returncode: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the migration_v2 agent-assisted validation and publish-readiness workflow."
    )
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument(
        "--env-config",
        default=str(ROOT / "configs" / "migration_v2" / "local_env.yaml"),
        help="Local environment config with a v2 section.",
    )
    parser.add_argument("--limit", type=int, default=200, help="Maximum unresolved queue items for the agent.")
    parser.add_argument("--issue-type", help="Optional validation queue issue_type filter.")
    parser.add_argument("--require-llm", action="store_true", help="Fail if the LLM probe or LLM agent calls fail.")
    parser.add_argument("--skip-llm-probe", action="store_true", help="Skip the small preflight LLM JSON call.")
    parser.add_argument(
        "--apply-low-risk",
        action="store_true",
        help="Apply only allowed low-risk agent proposals to the validation queue.",
    )
    parser.add_argument("--approved-by", help="Required when --apply-low-risk is used.")
    parser.add_argument(
        "--agent-policies",
        nargs="+",
        default=["accept", "quarantine"],
        help="Agent policies that may be applied by --apply-low-risk.",
    )
    parser.add_argument(
        "--min-agent-confidence",
        type=float,
        default=0.8,
        help="Minimum agent confidence for --apply-low-risk.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run checks and dry-run application without mutating validation queue decisions.",
    )
    return parser.parse_args()


def script_path(name: str) -> str:
    return str(ROOT / "scripts" / "migration_v2" / name)


def tail(value: str, max_chars: int = 3000) -> str:
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


def run_command(name: str, command: list[str], *, required: bool = True) -> WorkflowStep:
    LOGGER.info("Running %s", name)
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    step = WorkflowStep(
        name=name,
        command=command,
        status="completed" if completed.returncode == 0 else "failed",
        returncode=completed.returncode,
        stdout_tail=tail(completed.stdout),
        stderr_tail=tail(completed.stderr),
    )
    if required and completed.returncode != 0:
        raise SystemExit(f"{name} failed with exit code {completed.returncode}: {step.stderr_tail or step.stdout_tail}")
    return step


def probe_llm(require_llm: bool) -> dict[str, Any]:
    configured, reason = llm_config_status()
    payload: dict[str, Any] = {
        "configured": configured,
        "config_reason": reason,
        "status": "skipped" if not configured else "pending",
        "model_name": None,
        "error": None,
        "response": None,
    }
    if not configured:
        if require_llm:
            raise SystemExit(f"LLM is required, but config is unavailable: {reason}")
        return payload

    try:
        response, model_name = call_chat_llm(
            "Return strict JSON only.",
            'Return exactly this JSON shape with your own status text: {"status":"ok","purpose":"migration-agent-probe"}',
        )
        parsed = parse_json_object(response)
        payload.update(
            {
                "status": "completed",
                "model_name": model_name,
                "response": parsed,
            }
        )
    except Exception as exc:  # noqa: BLE001
        payload.update({"status": "failed", "error": str(exc)})
        if require_llm:
            raise SystemExit(f"LLM probe failed and --require-llm was set: {exc}") from exc
    return payload


def load_report(export_id: str, filename: str) -> dict[str, Any] | None:
    path = REPORT_ROOT / export_id / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def latest_agent_proposal_csv(export_id: str) -> Path:
    out_dir = REPORT_ROOT / export_id / "manual_review_csv"
    candidates = [path for path in out_dir.glob("10_agent_queue_proposals*.csv") if path.is_file()]
    if not candidates:
        return out_dir / "10_agent_queue_proposals.csv"
    return max(candidates, key=lambda path: path.stat().st_mtime)


def summarize_reports(export_id: str) -> dict[str, Any]:
    validation_queue = load_report(export_id, "validation_queue_report.json") or {}
    publish = load_report(export_id, "publish_report.json") or {}
    agent = load_report(export_id, "agent_validation_queue_proposals.json") or {}
    return {
        "agent": {
            "status": agent.get("status"),
            "mode": agent.get("mode"),
            "run_id": agent.get("run_id"),
            "reviewed_count": agent.get("reviewed_count"),
            "proposal_count": agent.get("proposal_count"),
            "llm_call_count": agent.get("llm_call_count"),
            "fallback_count": agent.get("fallback_count"),
            "errors": agent.get("errors") or [],
        },
        "validation_queue": {
            "status": validation_queue.get("status"),
            "blocking_item_count": validation_queue.get("blocking_item_count"),
            "nonblocking_item_count": validation_queue.get("nonblocking_item_count"),
            "policy_status_counts": validation_queue.get("policy_status_counts") or {},
            "blockers": validation_queue.get("blockers") or [],
        },
        "publish": {
            "status": publish.get("status"),
            "blockers": publish.get("blockers") or [],
        },
    }


def write_workflow_report(
    export_id: str,
    *,
    llm_probe: dict[str, Any],
    steps: list[WorkflowStep],
    summary: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[Path, Path]:
    payload = {
        "export_id": export_id,
        "dry_run": args.dry_run,
        "apply_low_risk": args.apply_low_risk,
        "approved_by": args.approved_by,
        "agent_policies": args.agent_policies,
        "min_agent_confidence": args.min_agent_confidence,
        "llm_probe": llm_probe,
        "steps": [asdict(step) for step in steps],
        "summary": summary,
    }
    json_path = write_json_report(export_id, "agent_publish_workflow_report.json", payload)
    md_path = write_markdown_report(
        export_id,
        "agent_publish_workflow_report.md",
        "Migration V2 Agent Publish Workflow Report",
        [
            (
                "LLM Probe",
                "\n".join(
                    [
                        f"- `configured`: {llm_probe.get('configured')}",
                        f"- `status`: `{llm_probe.get('status')}`",
                        f"- `model_name`: `{llm_probe.get('model_name')}`",
                        f"- `config_reason`: {llm_probe.get('config_reason')}",
                        f"- `error`: {llm_probe.get('error') or 'None'}",
                    ]
                ),
            ),
            (
                "Steps",
                "\n".join(
                    f"- `{step.name}`: `{step.status}` returncode={step.returncode}"
                    for step in steps
                ),
            ),
            (
                "Agent Summary",
                "\n".join(
                    [
                        f"- `status`: `{summary['agent'].get('status')}`",
                        f"- `mode`: `{summary['agent'].get('mode')}`",
                        f"- `reviewed_count`: {summary['agent'].get('reviewed_count')}",
                        f"- `proposal_count`: {summary['agent'].get('proposal_count')}",
                        f"- `llm_call_count`: {summary['agent'].get('llm_call_count')}",
                        f"- `fallback_count`: {summary['agent'].get('fallback_count')}",
                    ]
                ),
            ),
            (
                "Validation Queue",
                "\n".join(
                    [
                        f"- `status`: `{summary['validation_queue'].get('status')}`",
                        f"- `blocking_item_count`: {summary['validation_queue'].get('blocking_item_count')}",
                        f"- `nonblocking_item_count`: {summary['validation_queue'].get('nonblocking_item_count')}",
                        f"- `policy_status_counts`: `{summary['validation_queue'].get('policy_status_counts')}`",
                    ]
                ),
            ),
            (
                "Publish",
                "\n".join(
                    [f"- `status`: `{summary['publish'].get('status')}`"]
                    + [f"- {blocker}" for blocker in summary["publish"].get("blockers") or []]
                ),
            ),
        ],
    )
    return json_path, md_path


def main() -> None:
    args = parse_args()
    if args.apply_low_risk and not args.approved_by:
        raise SystemExit("--approved-by is required when --apply-low-risk is used.")

    steps: list[WorkflowStep] = []
    llm_probe = {"status": "skipped", "configured": None, "config_reason": "probe skipped"}
    if not args.skip_llm_probe:
        llm_probe = probe_llm(args.require_llm)

    base_args = ["--export-id", args.export_id, "--env-config", args.env_config]

    steps.append(run_command("populate_validation_queue", [sys.executable, script_path("16_populate_validation_queue.py"), *base_args]))

    agent_command = [
        sys.executable,
        script_path("18_run_validation_queue_agents.py"),
        *base_args,
        "--limit",
        str(args.limit),
    ]
    if args.issue_type:
        agent_command.extend(["--issue-type", args.issue_type])
    if args.require_llm:
        agent_command.append("--require-llm")
    if args.dry_run:
        agent_command.append("--dry-run")
    steps.append(run_command("run_validation_queue_agent", agent_command))

    if args.apply_low_risk:
        csv_path = latest_agent_proposal_csv(args.export_id)
        apply_command = [
            sys.executable,
            script_path("17_apply_validation_queue_decisions.py"),
            *base_args,
            "--csv-path",
            str(csv_path),
            "--approved-by",
            args.approved_by,
            "--use-agent-proposals",
            "--agent-policies",
            *args.agent_policies,
            "--min-agent-confidence",
            str(args.min_agent_confidence),
        ]
        if args.dry_run:
            apply_command.append("--dry-run")
        steps.append(run_command("apply_low_risk_agent_decisions", apply_command))

    steps.append(run_command("rebuild_validation_queue_report", [sys.executable, script_path("16_populate_validation_queue.py"), *base_args]))
    steps.append(
        run_command(
            "publish_dry_run",
            [sys.executable, script_path("10_publish_graph_version.py"), *base_args, "--dry-run"],
            required=False,
        )
    )

    summary = summarize_reports(args.export_id)
    if args.dry_run:
        summary["agent"] = {
            "status": "not_persisted_dry_run",
            "mode": "see run_validation_queue_agent step output",
            "run_id": None,
            "reviewed_count": None,
            "proposal_count": None,
            "llm_call_count": None,
            "fallback_count": None,
            "errors": [],
        }
    json_path, md_path = write_workflow_report(
        args.export_id,
        llm_probe=llm_probe,
        steps=steps,
        summary=summary,
        args=args,
    )
    LOGGER.info("Wrote %s and %s", json_path, md_path)


if __name__ == "__main__":
    main()
