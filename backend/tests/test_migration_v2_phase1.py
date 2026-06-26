from __future__ import annotations

import unittest
from uuid import uuid4

from pydantic import ValidationError

from app.migration_v2.agents.manifests import AGENT_MANIFESTS, AgentCapability
from app.migration_v2.orchestration.approval_service import SchemaApprovalCommand
from app.migration_v2.orchestration.checkpoints import normalize_postgres_connection_string
from app.migration_v2.orchestration.repository import (
    compute_export_fingerprint,
    workflow_idempotency_key,
)
from app.migration_v2.orchestration.state import MigrationRunState
from app.migration_v2.orchestration.workflow import build_phase1_graph, initialize_workflow


class MigrationPhaseOneTests(unittest.TestCase):
    def make_state(self) -> MigrationRunState:
        run_id = str(uuid4())
        return MigrationRunState(
            run_id=run_id,
            thread_id=f"migration-v2:{run_id}",
            export_id="test-export",
            export_path="C:/exports/test",
            export_fingerprint="f" * 64,
            contract_version="1.1.0",
        )

    def test_state_round_trip_is_json_safe(self):
        state = self.make_state()
        restored = MigrationRunState.model_validate(state.snapshot())
        self.assertEqual(restored.run_id, state.run_id)
        self.assertEqual(restored.current_phase, "received")

    def test_state_rejects_unknown_fields(self):
        payload = self.make_state().snapshot()
        payload["unsafe_untracked_state"] = True
        with self.assertRaises(ValidationError):
            MigrationRunState.model_validate(payload)

    def test_export_fingerprint_is_order_independent(self):
        files = [
            {"raw_table_name": "field", "file_path": "b.csv", "file_hash": "b"},
            {"raw_table_name": "source", "file_path": "a.csv", "file_hash": "a"},
        ]
        self.assertEqual(
            compute_export_fingerprint(files, "1.1.0"),
            compute_export_fingerprint(reversed(files), "1.1.0"),
        )

    def test_workflow_key_changes_with_contract(self):
        first = workflow_idempotency_key("export", "fingerprint", "1.0", "1.0")
        second = workflow_idempotency_key("export", "fingerprint", "1.1", "1.0")
        self.assertNotEqual(first, second)

    def test_agent_manifests_are_versioned_and_do_not_grant_shell(self):
        self.assertGreaterEqual(len(AGENT_MANIFESTS), 10)
        ids = {manifest.manifest_id for manifest in AGENT_MANIFESTS.values()}
        self.assertEqual(len(ids), len(AGENT_MANIFESTS))
        for manifest in AGENT_MANIFESTS.values():
            self.assertNotIn("shell", manifest.allowed_tools)
        intake = AGENT_MANIFESTS["ExportIntakeAgent"]
        self.assertIn(AgentCapability.WRITE_REGISTRY, intake.capabilities)
        self.assertEqual(intake.max_llm_calls, 0)

    def test_phase_one_node_validates_and_starts_state(self):
        result = initialize_workflow(self.make_state().snapshot())
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["current_phase"], "received")

    def test_phase_one_langgraph_compiles_and_runs(self):
        result = build_phase1_graph().invoke(self.make_state().snapshot())
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["current_phase"], "received")

    def test_connection_string_normalization(self):
        self.assertEqual(
            normalize_postgres_connection_string("postgresql+psycopg2://user:pass@db/name"),
            "postgresql://user:pass@db/name",
        )

    def test_schema_approval_requires_explicit_resolutions(self):
        with self.assertRaises(ValidationError):
            SchemaApprovalCommand.model_validate(
                {
                    "decision": "approve",
                    "decided_by": "reviewer",
                    "rationale": "Accept optional missing metadata.",
                    "resolutions": [],
                }
            )

    def test_schema_rejection_does_not_require_resolutions(self):
        command = SchemaApprovalCommand.model_validate(
            {
                "decision": "reject",
                "decided_by": "reviewer",
                "rationale": "Need source-owner evidence.",
            }
        )
        self.assertEqual(command.decision, "reject")


if __name__ == "__main__":
    unittest.main()
