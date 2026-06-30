from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.dqc.resolution.repository import _clean_json_value
from app.agent.contracts import DQCAgentProposal


def _json(value: Any) -> str:
    return json.dumps(_clean_json_value(value), allow_nan=False)


def start_agent_run(
    *,
    mode: str,
    message: str | None = None,
    source: str = "api",
) -> int | None:
    if SessionLocal is None:
        return None
    try:
        with SessionLocal() as db:
            row = db.execute(
                text(
                    """
                    INSERT INTO dqc_agent_run(agent_name, mode, status, source, message)
                    VALUES ('DQCResolutionAgent', :mode, 'running', :source, :message)
                    RETURNING id
                    """
                ),
                {"mode": mode, "source": source, "message": message},
            ).mappings().one()
            db.commit()
            return int(row["id"])
    except SQLAlchemyError:
        return None


def finish_agent_run(
    run_id: int | None,
    *,
    status: str,
    reviewed_count: int = 0,
    proposal_count: int = 0,
    llm_call_count: int = 0,
    fallback_count: int = 0,
    errors: list[str] | None = None,
) -> None:
    if run_id is None or SessionLocal is None:
        return
    try:
        with SessionLocal() as db:
            db.execute(
                text(
                    """
                    UPDATE dqc_agent_run
                    SET status = :status,
                        reviewed_count = :reviewed_count,
                        proposal_count = :proposal_count,
                        llm_call_count = :llm_call_count,
                        fallback_count = :fallback_count,
                        errors = CAST(:errors AS JSONB),
                        completed_at = now()
                    WHERE id = :run_id
                    """
                ),
                {
                    "run_id": run_id,
                    "status": status,
                    "reviewed_count": reviewed_count,
                    "proposal_count": proposal_count,
                    "llm_call_count": llm_call_count,
                    "fallback_count": fallback_count,
                    "errors": _json(errors or []),
                },
            )
            db.commit()
    except SQLAlchemyError:
        return


def save_agent_proposal(run_id: int | None, proposal: DQCAgentProposal) -> int | None:
    if run_id is None or SessionLocal is None:
        return None
    try:
        with SessionLocal() as db:
            row = db.execute(
                text(
                    """
                    INSERT INTO dqc_agent_proposal(
                        run_id, agent_name, subject_type, subject_id, proposed_action,
                        confidence, rationale, missing_evidence, human_question,
                        guardrail_actions, evidence, raw_model_response
                    ) VALUES (
                        :run_id, 'DQCResolutionAgent', :subject_type, :subject_id, :proposed_action,
                        :confidence, :rationale, CAST(:missing_evidence AS JSONB), :human_question,
                        CAST(:guardrail_actions AS JSONB), CAST(:evidence AS JSONB), :raw_model_response
                    )
                    RETURNING id
                    """
                ),
                {
                    "run_id": run_id,
                    "subject_type": proposal.subject_type,
                    "subject_id": proposal.subject_id,
                    "proposed_action": proposal.proposed_action,
                    "confidence": str(proposal.confidence) if proposal.confidence is not None else None,
                    "rationale": proposal.rationale,
                    "missing_evidence": _json(proposal.missing_evidence),
                    "human_question": proposal.human_question,
                    "guardrail_actions": _json(proposal.guardrail_actions),
                    "evidence": _json(proposal.evidence),
                    "raw_model_response": proposal.raw_model_response,
                },
            ).mappings().one()
            db.commit()
            return int(row["id"])
    except SQLAlchemyError:
        return None
