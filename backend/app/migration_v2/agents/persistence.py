from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.migration_v2.agents.execution import ExecutableAgentResult, MappingProposal


class AgentExecutionRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def start(self, *, export_id: str, workflow_run_id: str, agent_name: str, mode: str) -> int:
        with self.engine.begin() as conn:
            return int(
                conn.execute(
                    text(
                        """
                        INSERT INTO migration_agent_run(
                            export_id, workflow_run_id, agent_name, mode, status
                        )
                        VALUES (:export_id, CAST(:workflow_run_id AS uuid), :agent_name, :mode, 'running')
                        RETURNING id
                        """
                    ),
                    {
                        "export_id": export_id,
                        "workflow_run_id": workflow_run_id,
                        "agent_name": agent_name,
                        "mode": mode,
                    },
                ).scalar_one()
            )

    def finish(self, agent_run_id: int, result: ExecutableAgentResult) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE migration_agent_run
                    SET status = :status,
                        mode = :mode,
                        model_name = :model_name,
                        reviewed_count = :reviewed_count,
                        proposal_count = :proposal_count,
                        llm_call_count = :llm_call_count,
                        fallback_count = :fallback_count,
                        errors = CAST(:errors AS jsonb),
                        completed_at = now()
                    WHERE id = :agent_run_id
                    """
                ),
                {
                    "agent_run_id": agent_run_id,
                    "status": result.status,
                    "mode": result.mode,
                    "model_name": result.model_name,
                    "reviewed_count": int(result.summary.get("reviewed_count") or 0),
                    "proposal_count": len(result.proposals),
                    "llm_call_count": result.llm_call_count,
                    "fallback_count": result.fallback_count,
                    "errors": json.dumps(result.errors),
                },
            )

    def insert_mapping_proposals(
        self,
        *,
        export_id: str,
        workflow_run_id: str,
        agent_run_id: int,
        proposals: list[MappingProposal],
    ) -> None:
        with self.engine.begin() as conn:
            for proposal in proposals:
                conn.execute(
                    text(
                        """
                        INSERT INTO migration_schema_mapping_proposal(
                            export_id, workflow_run_id, agent_run_id, raw_table_name,
                            raw_column_name, current_canonical_field,
                            proposed_canonical_field, proposed_action, confidence,
                            rationale, missing_evidence, human_question,
                            candidate_columns, guardrail_actions, raw_model_response
                        )
                        VALUES (
                            :export_id, CAST(:workflow_run_id AS uuid), :agent_run_id,
                            :raw_table_name, :raw_column_name, :current_canonical_field,
                            :proposed_canonical_field, :proposed_action, :confidence,
                            :rationale, CAST(:missing_evidence AS jsonb), :human_question,
                            CAST(:candidate_columns AS jsonb), CAST(:guardrail_actions AS jsonb),
                            :raw_model_response
                        )
                        ON CONFLICT (agent_run_id, raw_table_name, raw_column_name) DO NOTHING
                        """
                    ),
                    {
                        "export_id": export_id,
                        "workflow_run_id": workflow_run_id,
                        "agent_run_id": agent_run_id,
                        "raw_table_name": proposal.raw_table_name,
                        "raw_column_name": proposal.raw_column_name,
                        "current_canonical_field": proposal.current_canonical_field,
                        "proposed_canonical_field": proposal.proposed_canonical_field,
                        "proposed_action": proposal.proposed_action,
                        "confidence": proposal.confidence,
                        "rationale": proposal.rationale,
                        "missing_evidence": json.dumps(proposal.missing_evidence),
                        "human_question": proposal.human_question,
                        "candidate_columns": json.dumps(proposal.candidate_columns, default=str),
                        "guardrail_actions": json.dumps(proposal.guardrail_actions),
                        "raw_model_response": proposal.raw_model_response,
                    },
                )
