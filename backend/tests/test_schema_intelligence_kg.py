from __future__ import annotations

import unittest

from app.migration_v2.agents.execution import MappingProposal
from app.migration_v2.agents.mapping_agent import enforce_mapping_guardrails
from app.migration_v2.orchestration.tool_runtime import AllowlistedToolRuntime
from app.migration_v2.schema_intelligence.projector import build_schema_projection


class SchemaIntelligenceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.contract = {
            "contract_version": "1.0.0",
            "global_rules": {
                "primary_id_column": "raw_id",
                "parent_id_column": "raw_parent",
                "status_column": "raw_status",
                "forbidden_join_columns": ["workspace_id"],
            },
            "typing_rules": {
                "identifier_columns": ["raw_id", "raw_parent"],
                "boolean_columns": ["is_active"],
                "integer_columns": [],
                "numeric_columns": [],
            },
            "unknown_columns_policy": {"preserve_unknown_columns": True},
            "tables": {
                "raw_customer": {
                    "canonical_table": "customer",
                    "object_type": "Structure",
                    "required_columns": ["raw_id"],
                    "columns": {
                        "customer_id": "raw_id",
                        "active": "is_active",
                        "missing_expected": "missing_raw",
                    },
                }
            },
        }

    def projection(self):
        return build_schema_projection(
            export_id="export-1",
            contract=self.contract,
            profiles=[
                {
                    "raw_table_name": "raw_customer",
                    "column_name": "raw_id",
                    "data_type_guess": "text",
                    "null_count": 0,
                    "non_null_count": 3,
                    "distinct_count": 3,
                    "sample_values": ["1", "2"],
                    "warnings": [],
                },
                {
                    "raw_table_name": "raw_customer",
                    "column_name": "is_active",
                    "data_type_guess": "boolean",
                    "null_count": 1,
                    "non_null_count": 2,
                    "distinct_count": 2,
                    "sample_values": ["true", "false"],
                    "warnings": [],
                },
            ],
            mapping_decisions=[
                {
                    "id": 1,
                    "raw_table_name": "raw_customer",
                    "raw_column_name": "raw_id",
                    "canonical_field": "customer_id",
                    "decision_type": "auto_exact_match",
                    "confidence": "1.0",
                    "requires_human_approval": False,
                }
            ],
            raw_files=[
                {
                    "raw_table_name": "raw_customer",
                    "file_path": "customer.csv",
                    "file_hash": "abc",
                }
            ],
        )

    def test_projection_has_only_table_columns_and_one_edge_per_column(self):
        projection = self.projection()
        self.assertEqual(len(projection.tables), 1)
        self.assertEqual(len(projection.columns), 3)
        self.assertEqual(projection.relationship_count, 3)
        self.assertTrue(all(column.table_key == projection.tables[0].table_key for column in projection.columns))

    def test_column_metadata_contains_alias_types_rules_and_description(self):
        column = next(item for item in self.projection().columns if item.column_name == "customer_id")
        self.assertEqual(column.name_variants, ["customer_id", "raw_id"])
        self.assertIn("profile:text", column.observed_types)
        self.assertIn("contract:identifier", column.observed_types)
        self.assertIn("stable_entity_identifier", column.rules)
        self.assertTrue(column.description)

    def test_missing_contract_column_is_retained_as_metadata_node(self):
        column = next(item for item in self.projection().columns if item.column_name == "missing_expected")
        self.assertFalse(column.present_in_latest_export)
        self.assertEqual(column.export_ids, [])
        self.assertIn("declared by contract but not observed", column.description)

    def test_column_properties_do_not_contain_relationship_payload(self):
        column = self.projection().columns[0]
        properties = column.properties()
        self.assertNotIn("table_key", properties)
        self.assertNotIn("relationships", properties)

    def test_mapping_guardrail_rejects_unobserved_target(self):
        proposal = MappingProposal(
            raw_table_name="raw_customer",
            raw_column_name="missing_raw",
            current_canonical_field="missing_expected",
            proposed_canonical_field="invented_column",
            proposed_action="map_to_observed_column",
            confidence=0.99,
            rationale="model suggestion",
            candidate_columns=[{"raw_column_name": "raw_id", "name_similarity": 0.3}],
        )
        guarded = enforce_mapping_guardrails(proposal, {"canonical_field": "missing_expected"})
        self.assertEqual(guarded.proposed_action, "needs_human")
        self.assertTrue(guarded.guardrail_actions)

    def test_tool_runtime_rejects_tool_outside_agent_manifest(self):
        runtime = AllowlistedToolRuntime(None, None, "postgresql://unused")
        with self.assertRaises(PermissionError):
            runtime.run(
                agent_name="MappingOntologyAgent",
                tool_name="profile_export",
                arguments=[],
            )


if __name__ == "__main__":
    unittest.main()
