from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from sqlalchemy import text

from _common import (
    config_section,
    ensure_tables,
    load_env_config,
    postgres_engine,
    postgres_engine_from_url,
    setup_logging,
)


LOGGER = setup_logging("migration_v2.apply_validation_queue_decisions")

DECISION_MAP = {
    "accept": ("accept", "approved"),
    "accepted": ("accept", "approved"),
    "approve": ("accept", "approved"),
    "quarantine": ("quarantine", "approved"),
    "quarantined": ("quarantine", "approved"),
    "exclude": ("exclude", "approved"),
    "excluded": ("exclude", "approved"),
    "repair": ("repair", "pending"),
    "repair_required": ("repair", "pending"),
    "repaired": ("repair", "resolved"),
    "resolved": ("repair", "resolved"),
    "block": ("block", "pending"),
    "blocked": ("block", "pending"),
    "needs_human": ("needs_human", "pending"),
    "defer": ("needs_human", "pending"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply reviewed validation-queue decisions from CSV.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--csv-path", required=True, help="CSV containing issue_id and reviewer_decision columns.")
    parser.add_argument("--approved-by", required=True, help="Human or policy approver identifier.")
    parser.add_argument("--env-config", help="Local environment config with a v2 section.")
    parser.add_argument("--dry-run", action="store_true", help="Validate CSV decisions without writing updates.")
    parser.add_argument(
        "--use-agent-proposals",
        action="store_true",
        help="Use agent_proposed_policy when reviewer_decision is blank.",
    )
    parser.add_argument(
        "--agent-policies",
        nargs="+",
        default=["accept", "quarantine"],
        help="Agent proposed policies allowed for --use-agent-proposals.",
    )
    parser.add_argument(
        "--min-agent-confidence",
        type=float,
        default=0.0,
        help="Minimum agent_confidence required for --use-agent-proposals.",
    )
    return parser.parse_args()


def engine_from_args(args: argparse.Namespace):
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
        if v2_config.get("postgres_url"):
            return postgres_engine_from_url(v2_config["postgres_url"])
    return postgres_engine()


def normalize_decision(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    key = value.strip().lower().replace(" ", "_").replace("-", "_")
    return DECISION_MAP.get(key)


def read_decisions(
    path: Path,
    *,
    use_agent_proposals: bool,
    agent_policies: set[str],
    min_agent_confidence: float,
) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"issue_id", "reviewer_decision"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV is missing required columns: {', '.join(sorted(missing))}")
        updates: list[dict[str, Any]] = []
        for row in reader:
            issue_id = (row.get("issue_id") or "").strip()
            decision_value = row.get("reviewer_decision")
            decision_source = "reviewer"
            if use_agent_proposals and not (decision_value or "").strip():
                agent_policy = (row.get("agent_proposed_policy") or "").strip().lower()
                agent_confidence = parse_float(row.get("agent_confidence"))
                if agent_policy in agent_policies and agent_confidence >= min_agent_confidence:
                    decision_value = agent_policy
                    decision_source = "agent_proposal"
            decision = normalize_decision(decision_value)
            if not issue_id or not decision:
                continue
            publish_policy, queue_status = decision
            reviewer_notes = (row.get("reviewer_notes") or "").strip()
            if decision_source == "agent_proposal" and not reviewer_notes:
                reviewer_notes = (
                    f"Applied agent proposal `{publish_policy}` from {path.name} "
                    "on explicit user instruction."
                )
            updates.append(
                {
                    "issue_id": issue_id,
                    "publish_policy": publish_policy,
                    "queue_status": queue_status,
                    "reviewer_notes": reviewer_notes,
                }
            )
    return updates


def parse_float(value: str | None) -> float:
    if value is None or not str(value).strip():
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def apply_updates(engine, export_id: str, updates: list[dict[str, Any]], approved_by: str) -> int:
    applied = 0
    with engine.begin() as conn:
        for update in updates:
            result = conn.execute(
                text(
                    """
                    UPDATE migration_validation_queue
                    SET publish_policy = :publish_policy,
                        queue_status = :queue_status,
                        approved_by = CASE
                            WHEN :queue_status IN ('approved', 'resolved') THEN :approved_by
                            ELSE approved_by
                        END,
                        approved_at = CASE
                            WHEN :queue_status = 'approved' THEN coalesce(approved_at, now())
                            ELSE approved_at
                        END,
                        resolved_at = CASE
                            WHEN :queue_status = 'resolved' THEN coalesce(resolved_at, now())
                            ELSE resolved_at
                        END,
                        rationale = CASE
                            WHEN :reviewer_notes <> '' THEN :reviewer_notes
                            ELSE rationale
                        END,
                        updated_at = now()
                    WHERE export_id = :export_id
                      AND issue_id = :issue_id
                      AND queue_status NOT IN ('approved', 'resolved')
                    """
                ),
                {"export_id": export_id, "approved_by": approved_by, **update},
            )
            rowcount = int(result.rowcount or 0)
            applied += rowcount
            if rowcount:
                conn.execute(
                    text(
                        """
                        UPDATE migration_agent_proposal
                        SET applied_to_queue = true
                        WHERE export_id = :export_id
                          AND issue_id = :issue_id
                          AND proposed_policy = :publish_policy
                        """
                    ),
                    {"export_id": export_id, **update},
                )
    return applied


def main() -> None:
    args = parse_args()
    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")
    updates = read_decisions(
        csv_path,
        use_agent_proposals=args.use_agent_proposals,
        agent_policies={policy.strip().lower() for policy in args.agent_policies},
        min_agent_confidence=args.min_agent_confidence,
    )
    if args.dry_run:
        LOGGER.info("Dry run found %s applicable decisions in %s", len(updates), csv_path)
        return
    if not updates:
        if args.use_agent_proposals:
            LOGGER.info(
                "No eligible agent proposals found in %s for policies=%s at min_confidence=%s; nothing to apply.",
                csv_path,
                ",".join(args.agent_policies),
                args.min_agent_confidence,
            )
            return
        raise SystemExit("No valid reviewer_decision values found in CSV.")
    engine = engine_from_args(args)
    ensure_tables(engine, ["migration_validation_queue"])
    applied = apply_updates(engine, args.export_id, updates, args.approved_by)
    LOGGER.info("Applied %s/%s validation queue decisions", applied, len(updates))


if __name__ == "__main__":
    main()
