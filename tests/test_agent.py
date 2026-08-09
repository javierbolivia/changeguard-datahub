"""Tests for the ChangeGuard autonomous agent."""

import unittest

from contract_sentinel.agent import (
    AgentResult,
    AgentStep,
    ChangeGuardAgent,
    StepStatus,
)
from contract_sentinel.datahub_mcp import DataHubMCPAdapter
from contract_sentinel.risk import Change


class AgentTests(unittest.TestCase):
    def test_agent_runs_full_pipeline_in_demo_mode(self):
        agent = ChangeGuardAgent()
        result = agent.run(Change("commerce.orders", "customer_id", "rename"))

        self.assertIsInstance(result, AgentResult)
        self.assertEqual(result.mode, "demo")
        self.assertEqual(len(result.steps), 11)
        self.assertIsNotNone(result.impact)
        self.assertIsNotNone(result.remediation)
        self.assertIsNotNone(result.report)
        self.assertEqual(result.impact.severity, "critical")
        self.assertGreaterEqual(result.impact.score, 80)

    def test_agent_emits_step_callbacks(self):
        events = []
        agent = ChangeGuardAgent(on_step_update=lambda s: events.append(s.status))
        agent.run(Change("commerce.orders", "customer_id", "drop"))

        # Each step emits RUNNING + final status = at least 22 events (11 steps)
        self.assertGreaterEqual(len(events), 22)
        # All steps complete (SUCCESS or SKIPPED)
        final_statuses = [events[i] for i in range(1, len(events), 2)]
        for status in final_statuses:
            self.assertIn(status, {StepStatus.SUCCESS, StepStatus.SKIPPED})

    def test_agent_handles_add_with_existing_downstream(self):
        """An 'add' with existing downstream assets still shows medium risk
        because the column's downstream consumers exist in demo fixtures."""
        agent = ChangeGuardAgent()
        result = agent.run(Change("commerce.orders", "notes", "add"))

        # add base is 5, but fixtures add downstream points
        self.assertIn(result.impact.severity, {"low", "medium"})
        self.assertLessEqual(result.impact.score, 50)

    def test_agent_skips_writeback_without_confirmation(self):
        agent = ChangeGuardAgent()
        result = agent.run(
            Change("commerce.orders", "customer_id", "rename"),
            confirm_writeback=False,
        )

        wb_step = next(s for s in result.steps if s.name == "writeback")
        self.assertEqual(wb_step.status, StepStatus.SKIPPED)
        self.assertEqual(wb_step.result["reason"], "user confirmation required")

    def test_persist_decision_skipped_without_confirmation(self):
        calls = []
        agent = ChangeGuardAgent(writeback_fn=lambda *a: calls.append(a))
        result = agent.run(
            Change("commerce.orders", "customer_id", "drop"),
            confirm_writeback=False,
        )

        persist_step = next(s for s in result.steps if s.name == "persist_decision")
        self.assertEqual(persist_step.status, StepStatus.SKIPPED)
        self.assertEqual(calls, [])
        self.assertFalse(result.writeback_success)

    def test_persist_decision_skipped_without_writeback_fn(self):
        agent = ChangeGuardAgent()
        result = agent.run(
            Change("commerce.orders", "customer_id", "drop"),
            confirm_writeback=True,
        )

        persist_step = next(s for s in result.steps if s.name == "persist_decision")
        self.assertEqual(persist_step.status, StepStatus.SKIPPED)
        self.assertFalse(result.writeback_success)

    def test_persist_decision_calls_writeback_fn_with_confirmation(self):
        calls = []

        def fake_writeback(dataset_urn, decision, score, severity, operation, column):
            calls.append((dataset_urn, decision, score, severity, operation, column))
            return {"dataset_urn": dataset_urn}

        agent = ChangeGuardAgent(writeback_fn=fake_writeback)
        result = agent.run(
            Change("commerce.orders", "customer_id", "drop"),
            confirm_writeback=True,
        )

        persist_step = next(s for s in result.steps if s.name == "persist_decision")
        self.assertEqual(persist_step.status, StepStatus.SUCCESS)
        self.assertTrue(result.writeback_success)
        self.assertEqual(len(calls), 1)
        dataset_urn, decision, score, severity, operation, column = calls[0]
        self.assertEqual(
            dataset_urn,
            "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)",
        )
        self.assertEqual(decision, "BLOCK")
        self.assertEqual(operation, "drop")
        self.assertEqual(column, "customer_id")
        self.assertEqual(score, result.impact.score)
        self.assertEqual(severity, result.impact.severity)

    def test_persist_decision_failure_does_not_crash_agent(self):
        def failing_writeback(*args):
            raise ConnectionError("DataHub GMS unreachable")

        agent = ChangeGuardAgent(writeback_fn=failing_writeback)
        result = agent.run(
            Change("commerce.orders", "customer_id", "drop"),
            confirm_writeback=True,
        )

        persist_step = next(s for s in result.steps if s.name == "persist_decision")
        self.assertEqual(persist_step.status, StepStatus.FAILED)
        self.assertIn("unreachable", persist_step.error)
        self.assertFalse(result.writeback_success)
        # The rest of the result (impact/report) must remain intact.
        self.assertIsNotNone(result.impact)

    def test_agent_all_steps_have_positive_duration(self):
        agent = ChangeGuardAgent()
        result = agent.run(Change("commerce.orders", "customer_id", "type_change"))

        for step in result.steps:
            if step.status != StepStatus.PENDING:
                self.assertGreaterEqual(step.duration_ms, 0)

    def test_invalid_operation_fails_gracefully(self):
        agent = ChangeGuardAgent()
        result = agent.run(Change("commerce.orders", "id", "destroy"))

        self.assertEqual(result.steps[0].status, StepStatus.FAILED)
        self.assertIn("destroy", result.steps[0].error)
        # Agent stops after first failure
        self.assertEqual(len(result.steps), 1)


class LiveModeShapeTests(unittest.TestCase):
    """Tests using the REAL mcp-server-datahub response shapes, as observed
    against a live local DataHub instance:
      - search           -> {"searchResults": [{"entity": {"urn": ...}}]}
      - get_lineage       -> {"downstreams": {"searchResults": [...]}}
    """

    TOOLS = {
        "search",
        "get_entities",
        "list_schema_fields",
        "get_lineage",
        "get_lineage_paths_between",
    }

    def _make_adapter(self, caller):
        return DataHubMCPAdapter(caller, self.TOOLS)

    def _run_zero_confirmed(
        self,
        change,
        *,
        include_potential=False,
        writeback_fn=None,
        confirm_writeback=False,
    ):
        resolved_urn = (
            "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"
        )

        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": resolved_urn,
                                "properties": {"name": "commerce.orders"},
                            }
                        }
                    ]
                }
            if name == "list_schema_fields":
                return {
                    "fields": [
                        {"fieldPath": "order_id"},
                        {"fieldPath": "customer_id"},
                    ]
                }
            if name == "get_entities":
                return []
            if name == "get_lineage" and "column" in arguments:
                return {"downstreams": {"searchResults": []}}
            if name == "get_lineage":
                potential = []
                if include_potential:
                    potential.append(
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.sales_summary,PROD)",
                                "name": "analytics.sales_summary",
                            }
                        }
                    )
                return {"downstreams": {"searchResults": potential}}
            raise AssertionError(f"unexpected tool call: {name}")

        agent = ChangeGuardAgent(
            mcp_adapter=self._make_adapter(caller), writeback_fn=writeback_fn
        )
        return agent.run(change, confirm_writeback=confirm_writeback)

    def test_live_drop_with_zero_confirmed_lineage_requires_review(self):
        result = self._run_zero_confirmed(
            Change("commerce.orders", "customer_id", "drop")
        )

        self.assertEqual(result.decision, "REVIEW")
        self.assertEqual((result.impact.score, result.impact.severity), (55, "medium"))
        self.assertIn("Insufficient confirmed", result.decision_reason)
        self.assertIn("Decision: **REVIEW**", result.report)

    def test_live_rename_with_zero_confirmed_lineage_requires_review(self):
        result = self._run_zero_confirmed(
            Change("commerce.orders", "customer_id", "rename", "cust_key")
        )

        self.assertEqual(result.decision, "REVIEW")
        self.assertEqual((result.impact.score, result.impact.severity), (45, "medium"))

    def test_live_type_change_with_zero_confirmed_lineage_requires_review(self):
        result = self._run_zero_confirmed(
            Change("commerce.orders", "customer_id", "type_change", "string")
        )

        self.assertEqual(result.decision, "REVIEW")
        self.assertEqual((result.impact.score, result.impact.severity), (35, "medium"))

    def test_live_add_with_zero_confirmed_lineage_remains_allow(self):
        result = self._run_zero_confirmed(
            Change("commerce.orders", "promo_code", "add", "string")
        )

        self.assertEqual(result.decision, "ALLOW")
        self.assertEqual((result.impact.score, result.impact.severity), (5, "low"))

    def test_zero_confirmed_with_potential_is_review_and_potential_is_not_scored(self):
        result = self._run_zero_confirmed(
            Change("commerce.orders", "customer_id", "drop"),
            include_potential=True,
        )

        self.assertEqual(result.decision, "REVIEW")
        self.assertEqual(result.impact.score, 55)
        self.assertEqual(result.downstream_assets, [])
        self.assertEqual(
            [asset["name"] for asset in result.potential_downstream_assets],
            ["analytics.sales_summary"],
        )
        self.assertIn("table-level potential", result.decision_reason)

    def test_resolve_urn_parses_real_search_shape(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders_archive,PROD)",
                                "properties": {"name": "commerce.orders_archive"},
                            }
                        },
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)",
                                "properties": {"name": "commerce.orders"},
                            }
                        }
                    ]
                }
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(Change("commerce.orders", "customer_id", "rename"))

        resolve_step = next(s for s in result.steps if s.name == "resolve_urn")
        self.assertEqual(resolve_step.result["source"], "datahub_search")
        self.assertEqual(
            resolve_step.result["urn"],
            "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)",
        )

    def test_resolve_urn_rejects_ambiguous_exact_candidates(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)",
                                "properties": {"name": "commerce.orders"},
                            }
                        },
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:bigquery,commerce.orders,PROD)",
                                "properties": {"name": "commerce.orders"},
                            }
                        },
                    ]
                }
            raise AssertionError(f"unexpected tool call: {name}")

        result = ChangeGuardAgent(mcp_adapter=self._make_adapter(caller)).run(
            Change("commerce.orders", "customer_id", "drop")
        )

        resolve_step = next(s for s in result.steps if s.name == "resolve_urn")
        self.assertEqual(resolve_step.status, StepStatus.FAILED)
        self.assertIn("Ambiguous", resolve_step.error)
        self.assertIsNone(result.impact)

    def test_resolve_urn_rejects_fuzzy_only_results(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders_archive,PROD)",
                                "properties": {"name": "commerce.orders_archive"},
                            }
                        }
                    ]
                }
            raise AssertionError(f"unexpected tool call: {name}")

        result = ChangeGuardAgent(mcp_adapter=self._make_adapter(caller)).run(
            Change("commerce.orders", "customer_id", "drop")
        )

        resolve_step = next(s for s in result.steps if s.name == "resolve_urn")
        self.assertEqual(resolve_step.status, StepStatus.FAILED)
        self.assertIn("No exact dataset match", resolve_step.error)
        self.assertIsNone(result.impact)

    def test_resolve_urn_rejects_exact_result_missing_urn(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"properties": {"name": "commerce.orders"}}}
                    ]
                }
            raise AssertionError(f"unexpected tool call: {name}")

        result = ChangeGuardAgent(mcp_adapter=self._make_adapter(caller)).run(
            Change("commerce.orders", "customer_id", "drop")
        )

        resolve_step = next(s for s in result.steps if s.name == "resolve_urn")
        self.assertEqual(resolve_step.status, StepStatus.FAILED)
        self.assertIn("missing URN", resolve_step.error)
        self.assertIsNone(result.impact)

    def test_fetch_lineage_parses_real_downstreams_shape(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_lineage":
                if "column" in arguments:
                    return {
                        "downstreams": {
                            "searchResults": [
                                {
                                    "entity": {
                                        "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.customer_orders,PROD)",
                                        "properties": {"name": "analytics.customer_orders"},
                                    },
                                    "degree": 1,
                                    "lineageColumns": ["customer_id"],
                                }
                            ]
                        }
                    }
                return {"downstreams": {"searchResults": []}}
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(Change("commerce.orders", "customer_id", "rename"))

        self.assertEqual(len(result.downstream_assets), 1)
        asset = result.downstream_assets[0]
        self.assertEqual(asset["name"], "analytics.customer_orders")
        self.assertEqual(
            asset["urn"],
            "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.customer_orders,PROD)",
        )
        lineage_step = next(s for s in result.steps if s.name == "fetch_lineage")
        self.assertEqual(lineage_step.result["source"], "datahub_mcp")
        self.assertEqual(lineage_step.result["assets_found"], 1)

    def test_duplicate_confirmed_lineage_does_not_inflate_score(self):
        downstream_urn = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.customer_orders,PROD)"

        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)",
                                "properties": {"name": "commerce.orders"},
                            }
                        }
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_lineage":
                if "column" not in arguments:
                    return {"downstreams": {"searchResults": []}}
                duplicate = {
                    "entity": {
                        "urn": downstream_urn,
                        "properties": {"name": "analytics.customer_orders"},
                    },
                    "lineageColumns": ["customer_id"],
                }
                return {"downstreams": {"searchResults": [duplicate, duplicate]}}
            raise AssertionError(f"unexpected tool call: {name}")

        result = ChangeGuardAgent(mcp_adapter=self._make_adapter(caller)).run(
            Change("commerce.orders", "customer_id", "rename", "cust_key")
        )

        self.assertEqual(len(result.downstream_assets), 1)
        self.assertEqual(result.downstream_assets[0]["urn"], downstream_urn)
        self.assertEqual(result.impact.score, 50)
        self.assertEqual(len(result.impact.affected_assets), 1)

    def test_distinct_confirmed_lineage_assets_are_retained(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)",
                                "properties": {"name": "commerce.orders"},
                            }
                        }
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_lineage":
                if "column" not in arguments:
                    return {"downstreams": {"searchResults": []}}
                return {
                    "downstreams": {
                        "searchResults": [
                            {
                                "entity": {
                                    "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.customer_orders,PROD)",
                                    "properties": {"name": "analytics.customer_orders"},
                                },
                                "lineageColumns": ["customer_id"],
                            },
                            {
                                "entity": {
                                    "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.customer_ltv,PROD)",
                                    "properties": {"name": "analytics.customer_ltv"},
                                },
                                "lineageColumns": ["customer_id"],
                            },
                        ]
                    }
                }
            raise AssertionError(f"unexpected tool call: {name}")

        result = ChangeGuardAgent(mcp_adapter=self._make_adapter(caller)).run(
            Change("commerce.orders", "customer_id", "rename", "cust_key")
        )

        self.assertEqual(len(result.downstream_assets), 2)
        self.assertEqual(result.impact.score, 55)

    def test_live_mode_never_falls_back_to_fixtures_on_lineage_error(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_lineage":
                raise ConnectionError("DataHub GMS unreachable")
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(Change("commerce.orders", "customer_id", "rename"))

        lineage_step = next(s for s in result.steps if s.name == "fetch_lineage")
        self.assertEqual(lineage_step.status, StepStatus.FAILED)
        self.assertNotEqual(lineage_step.status, StepStatus.WARNING)
        # Live mode must not substitute demo fixtures on failure.
        self.assertEqual(result.downstream_assets, [])
        self.assertIsNone(result.impact)

    def test_live_mode_never_falls_back_to_fixtures_on_search_error(self):
        async def caller(name, arguments):
            if name == "search":
                raise ConnectionError("DataHub GMS unreachable")
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(Change("commerce.orders", "customer_id", "rename"))

        resolve_step = next(s for s in result.steps if s.name == "resolve_urn")
        self.assertEqual(resolve_step.status, StepStatus.FAILED)
        self.assertIsNone(result.impact)
        self.assertEqual(result.downstream_assets, [])

    def test_live_mode_stops_when_dataset_not_found(self):
        """search returning zero results (no error) must stop the agent,
        not construct a guessed URN and continue."""

        async def caller(name, arguments):
            if name == "search":
                return {"total": 0}
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(
            Change("commerce.this_does_not_exist", "customer_id", "rename")
        )

        resolve_step = next(s for s in result.steps if s.name == "resolve_urn")
        self.assertEqual(resolve_step.status, StepStatus.FAILED)
        self.assertIn("not found", resolve_step.error)
        self.assertIsNone(result.impact)
        self.assertFalse(result.writeback_success)
        # Only the parse + resolve_urn steps should have run.
        self.assertEqual(len(result.steps), 2)

    def test_live_mode_stops_when_column_not_found(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {
                    "fields": [
                        {"fieldPath": "order_id"},
                        {"fieldPath": "customer_id"},
                        {"fieldPath": "amount"},
                    ]
                }
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(
            Change("commerce.orders", "this_column_does_not_exist", "rename")
        )

        validate_step = next(s for s in result.steps if s.name == "validate_schema")
        self.assertEqual(validate_step.status, StepStatus.FAILED)
        self.assertIn("this_column_does_not_exist", validate_step.error)
        self.assertIn("not found", validate_step.error)
        self.assertIsNone(result.impact)
        self.assertFalse(result.writeback_success)

    def test_live_mode_rejects_rename_to_existing_column(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {
                    "fields": [
                        {"fieldPath": "order_id"},
                        {"fieldPath": "customer_id"},
                        {"fieldPath": "amount"},
                    ]
                }
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(
            Change("commerce.orders", "customer_id", "rename", new_type="order_id")
        )

        validate_step = next(s for s in result.steps if s.name == "validate_schema")
        self.assertEqual(validate_step.status, StepStatus.FAILED)
        self.assertIn("already exists", validate_step.error)
        self.assertIsNone(result.impact)
        self.assertFalse(result.writeback_success)

    def test_live_mode_passes_schema_validation_for_valid_column(self):
        """Sanity check: a real, existing column must not be rejected."""

        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {
                    "fields": [
                        {"fieldPath": "order_id"},
                        {"fieldPath": "customer_id"},
                        {"fieldPath": "amount"},
                    ]
                }
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(
            Change("commerce.orders", "customer_id", "rename", new_type="cust_key")
        )

        validate_step = next(s for s in result.steps if s.name == "validate_schema")
        self.assertEqual(validate_step.status, StepStatus.SUCCESS)
        self.assertIsNotNone(result.impact)

    def test_live_add_accepts_a_new_column_absent_from_schema(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)",
                                "properties": {"name": "commerce.orders"},
                            }
                        }
                    ]
                }
            if name == "list_schema_fields":
                return {
                    "fields": [
                        {"fieldPath": "order_id"},
                        {"fieldPath": "customer_id"},
                    ]
                }
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            raise AssertionError(f"unexpected tool call: {name}")

        result = ChangeGuardAgent(mcp_adapter=self._make_adapter(caller)).run(
            Change("commerce.orders", "promo_code", "add")
        )

        validate_step = next(s for s in result.steps if s.name == "validate_schema")
        self.assertEqual(validate_step.status, StepStatus.SUCCESS)
        self.assertIsNotNone(result.impact)
        self.assertEqual(result.impact.score, 5)
        self.assertEqual(result.impact.severity, "low")

    def test_live_add_rejects_collision_with_existing_column(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)",
                                "properties": {"name": "commerce.orders"},
                            }
                        }
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "promo_code"}]}
            raise AssertionError(f"unexpected tool call: {name}")

        result = ChangeGuardAgent(mcp_adapter=self._make_adapter(caller)).run(
            Change("commerce.orders", "promo_code", "add")
        )

        validate_step = next(s for s in result.steps if s.name == "validate_schema")
        self.assertEqual(validate_step.status, StepStatus.FAILED)
        self.assertIn("already exists", validate_step.error)
        self.assertIsNone(result.impact)

    def test_live_drop_still_requires_existing_source_column(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)",
                                "properties": {"name": "commerce.orders"},
                            }
                        }
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "order_id"}]}
            raise AssertionError(f"unexpected tool call: {name}")

        result = ChangeGuardAgent(mcp_adapter=self._make_adapter(caller)).run(
            Change("commerce.orders", "missing_column", "drop")
        )

        validate_step = next(s for s in result.steps if s.name == "validate_schema")
        self.assertEqual(validate_step.status, StepStatus.FAILED)
        self.assertIn("not found", validate_step.error)
        self.assertIsNone(result.impact)

    def test_potential_downstream_confirmed_and_excludes_duplicates(self):
        """A dataset with confirmed column-level lineage must appear only
        in downstream_assets; a dataset only visible in the table-level
        query must appear only in potential_downstream_assets, never both,
        and must never affect the risk score."""

        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_lineage":
                if "column" in arguments:
                    # Confirmed column-level: only customer_orders
                    return {
                        "downstreams": {
                            "searchResults": [
                                {
                                    "entity": {
                                        "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.customer_orders,PROD)",
                                        "name": "analytics.customer_orders",
                                    },
                                    "degree": 1,
                                }
                            ]
                        }
                    }
                # Table-level: both customer_orders (degree 1) and
                # sales_summary (degree 2) - customer_orders must be
                # de-duplicated since it is already confirmed.
                return {
                    "downstreams": {
                        "searchResults": [
                            {
                                "entity": {
                                    "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.customer_orders,PROD)",
                                    "name": "analytics.customer_orders",
                                },
                                "degree": 1,
                            },
                            {
                                "entity": {
                                    "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.sales_summary,PROD)",
                                    "name": "analytics.sales_summary",
                                },
                                "degree": 2,
                            },
                        ]
                    }
                }
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(Change("commerce.orders", "customer_id", "rename", "cust_key"))

        # Confirmed impact: only customer_orders.
        confirmed_urns = {a["urn"] for a in result.downstream_assets}
        self.assertEqual(
            confirmed_urns,
            {"urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.customer_orders,PROD)"},
        )

        # Potential downstream: only sales_summary (customer_orders excluded
        # as a duplicate of the confirmed set).
        potential_urns = {a["urn"] for a in result.potential_downstream_assets}
        self.assertEqual(
            potential_urns,
            {"urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.sales_summary,PROD)"},
        )

        # No overlap between the two sets.
        self.assertEqual(confirmed_urns & potential_urns, set())

        # Risk score must be based only on the confirmed set (1 asset):
        # matches the already-verified real scenario (50/100, medium, ALLOW).
        self.assertEqual(result.impact.score, 50)
        self.assertEqual(result.impact.severity, "medium")
        self.assertEqual(len(result.impact.affected_assets), 1)

    def test_potential_downstream_warning_preserves_drop_and_rename_decisions(self):
        """A best-effort table-level failure is WARNING, never policy input."""

        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_entities":
                return []
            if name == "get_lineage":
                if "column" in arguments:
                    return {
                        "downstreams": {
                            "searchResults": [
                                {
                                    "entity": {
                                        "urn": "urn:confirmed",
                                        "name": "analytics.customer_orders",
                                    }
                                }
                            ]
                        }
                    }
                raise ConnectionError("table-level lineage unavailable")
            raise AssertionError(f"unexpected tool call: {name}")

        cases = (
            (Change("commerce.orders", "customer_id", "drop"), 60, "high", "BLOCK"),
            (
                Change("commerce.orders", "customer_id", "rename", "cust_key"),
                50,
                "medium",
                "ALLOW",
            ),
        )
        for change, score, severity, decision in cases:
            with self.subTest(operation=change.operation):
                adapter = self._make_adapter(caller)
                result = ChangeGuardAgent(mcp_adapter=adapter).run(change)
                potential_step = next(
                    s for s in result.steps if s.name == "fetch_potential_downstream"
                )
                decision_step = next(s for s in result.steps if s.name == "decision")

                self.assertEqual(potential_step.status, StepStatus.WARNING)
                self.assertNotEqual(potential_step.status, StepStatus.FAILED)
                self.assertIn("table-level lineage unavailable", potential_step.error)
                self.assertIn("Analysis continued", potential_step.result["reason"])
                self.assertEqual(
                    (result.impact.score, result.impact.severity), (score, severity)
                )
                self.assertEqual(decision_step.result["decision"], decision)
                self.assertEqual(len(result.downstream_assets), 1)
                self.assertEqual(result.potential_downstream_assets, [])
                self.assertEqual(result.warnings, [potential_step.result["reason"]])
                self.assertIn("## Partial analysis warnings", result.report)
                self.assertIn(potential_step.result["reason"], result.report)

    def test_writeback_skipped_when_save_document_unavailable(self):
        """save_document is an optional Document Tool that mcp-server-datahub
        hides when the target DataHub instance has no documents yet. Its
        absence must be reported as SKIPPED with a clear reason, never as
        FAILED — this is a server capability limitation, not a ChangeGuard
        error."""

        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            raise AssertionError(f"unexpected tool call: {name}")

        tools_without_save_document = self.TOOLS  # base TOOLS has no save_document
        adapter = DataHubMCPAdapter(caller, tools_without_save_document)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(
            Change("commerce.orders", "customer_id", "drop"),
            confirm_writeback=True,
        )

        wb_step = next(s for s in result.steps if s.name == "writeback")
        self.assertEqual(wb_step.status, StepStatus.SKIPPED)
        self.assertEqual(
            wb_step.result["reason"],
            "save_document not available on this DataHub server",
        )
        # The rest of the pipeline (score/decision) must be unaffected.
        self.assertIsNotNone(result.impact)
        decision_step = next(s for s in result.steps if s.name == "decision")
        self.assertEqual(decision_step.status, StepStatus.SUCCESS)

    def test_writeback_succeeds_when_save_document_available(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            if name == "save_document":
                return {"success": True}
            raise AssertionError(f"unexpected tool call: {name}")

        tools_with_save_document = self.TOOLS | {"save_document"}
        adapter = DataHubMCPAdapter(caller, tools_with_save_document)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(
            Change("commerce.orders", "customer_id", "drop"),
            confirm_writeback=True,
        )

        wb_step = next(s for s in result.steps if s.name == "writeback")
        self.assertEqual(wb_step.status, StepStatus.SUCCESS)

    def test_writeback_failed_when_save_document_call_raises(self):
        """save_document is available but the actual call fails - this is
        a real failure and must surface as FAILED, not SKIPPED."""

        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            if name == "save_document":
                raise ConnectionError("DataHub GMS unreachable")
            raise AssertionError(f"unexpected tool call: {name}")

        tools_with_save_document = self.TOOLS | {"save_document"}
        adapter = DataHubMCPAdapter(caller, tools_with_save_document)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(
            Change("commerce.orders", "customer_id", "drop"),
            confirm_writeback=True,
        )

        wb_step = next(s for s in result.steps if s.name == "writeback")
        self.assertEqual(wb_step.status, StepStatus.FAILED)
        self.assertIn("unreachable", wb_step.error)
        # The rest of the pipeline (score/decision) must be unaffected.
        self.assertIsNotNone(result.impact)

    def test_persist_decision_receives_exact_resolved_dataset_urn(self):
        resolved_urn = (
            "urn:li:dataset:(urn:li:dataPlatform:bigquery,commerce.orders,DEV)"
        )
        persisted = []

        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": resolved_urn,
                                "properties": {"name": "commerce.orders"},
                            }
                        }
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            raise AssertionError(f"unexpected tool call: {name}")

        def fake_writeback(dataset_urn, decision, *args):
            persisted.append((dataset_urn, decision))
            return {"dataset_urn": dataset_urn}

        agent = ChangeGuardAgent(
            mcp_adapter=self._make_adapter(caller), writeback_fn=fake_writeback
        )
        result = agent.run(
            Change("commerce.orders", "customer_id", "drop"),
            confirm_writeback=True,
        )

        persist_step = next(s for s in result.steps if s.name == "persist_decision")
        self.assertEqual(persist_step.status, StepStatus.SUCCESS)
        self.assertEqual(persisted, [(resolved_urn, "REVIEW")])
        self.assertEqual(result.decision, "REVIEW")

    def test_previous_context_parsed_and_does_not_affect_score(self):
        """When get_entities returns real changeguard_* custom properties
        from a prior run, the agent must surface them as previous_context
        without letting them influence the current score/decision."""

        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_entities":
                return [
                    {
                        "properties": {
                            "customProperties": [
                                {"key": "changeguard_decision", "value": "REVIEW"},
                                {"key": "changeguard_risk_score", "value": "55"},
                                {"key": "changeguard_severity", "value": "medium"},
                                {"key": "changeguard_operation", "value": "drop"},
                                {"key": "changeguard_column", "value": "customer_id"},
                                {"key": "changeguard_timestamp", "value": "2026-08-09T02:40:44+00:00"},
                            ]
                        }
                    }
                ]
            if name == "get_lineage" and "column" in arguments:
                return {
                    "downstreams": {
                        "searchResults": [
                            {
                                "entity": {
                                    "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.customer_orders,PROD)",
                                    "name": "analytics.customer_orders",
                                }
                            }
                        ]
                    }
                }
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        # Different operation from the persisted one (drop) - rename here.
        result = agent.run(Change("commerce.orders", "customer_id", "rename", "cust_key"))

        self.assertIsNotNone(result.previous_context)
        self.assertEqual(result.previous_context.decision, "REVIEW")
        self.assertEqual(result.previous_context.risk_score, 55)
        self.assertEqual(result.previous_context.operation, "drop")

        # The CURRENT analysis must be unaffected by the persisted REVIEW:
        # confirmed current lineage produces the verified fresh ALLOW result.
        self.assertEqual(result.impact.score, 50)
        self.assertEqual(result.decision, "ALLOW")
        context_step = next(s for s in result.steps if s.name == "fetch_previous_context")
        self.assertEqual(context_step.status, StepStatus.SUCCESS)

    def test_no_persisted_properties_yields_none_context_not_error(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_entities":
                return [{"properties": {"customProperties": []}}]
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(Change("commerce.orders", "customer_id", "rename", "cust_key"))

        self.assertIsNone(result.previous_context)
        self.assertFalse(result.previously_evaluated)
        context_step = next(s for s in result.steps if s.name == "fetch_previous_context")
        self.assertEqual(context_step.status, StepStatus.SUCCESS)
        # No previous context must not prevent a normal decision.
        self.assertIsNotNone(result.impact)

    def test_matching_dataset_column_operation_marks_previously_evaluated(self):
        """Same dataset (implicit - only one dataset is evaluated per
        run), column, and operation as the persisted context must set
        previously_evaluated=True, without skipping the real analysis."""

        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_entities":
                return [
                    {
                        "properties": {
                            "customProperties": [
                                {"key": "changeguard_decision", "value": "BLOCK"},
                                {"key": "changeguard_risk_score", "value": "60"},
                                {"key": "changeguard_severity", "value": "high"},
                                {"key": "changeguard_operation", "value": "drop"},
                                {"key": "changeguard_column", "value": "customer_id"},
                                {"key": "changeguard_timestamp", "value": "2026-08-09T02:40:44+00:00"},
                            ]
                        }
                    }
                ]
            if name == "get_lineage":
                return {
                    "downstreams": {
                        "searchResults": [
                            {
                                "entity": {
                                    "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.customer_orders,PROD)",
                                    "name": "analytics.customer_orders",
                                },
                            }
                        ]
                    }
                }
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        # Same column + operation (drop / customer_id) as the persisted context.
        result = agent.run(Change("commerce.orders", "customer_id", "drop"))

        self.assertTrue(result.previously_evaluated)
        # The pipeline still ran validate_schema and fetch_lineage - it
        # was not skipped because a previous decision existed.
        validate_step = next(s for s in result.steps if s.name == "validate_schema")
        lineage_step = next(s for s in result.steps if s.name == "fetch_lineage")
        self.assertEqual(validate_step.status, StepStatus.SUCCESS)
        self.assertEqual(lineage_step.status, StepStatus.SUCCESS)
        # Real, freshly computed decision, matching the documented scenario.
        self.assertEqual(result.impact.score, 60)
        self.assertEqual(result.impact.severity, "high")

    def test_get_entities_failure_does_not_break_pipeline(self):
        """fetch_previous_context is best-effort: a get_entities failure
        must degrade to previous_context=None and still let schema
        validation, lineage, and the decision complete normally."""

        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_entities":
                raise ConnectionError("DataHub GMS unreachable")
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(Change("commerce.orders", "customer_id", "rename", "cust_key"))

        context_step = next(s for s in result.steps if s.name == "fetch_previous_context")
        self.assertEqual(context_step.status, StepStatus.FAILED)
        self.assertIsNone(result.previous_context)
        self.assertFalse(result.previously_evaluated)

        # The rest of the pipeline must complete normally.
        validate_step = next(s for s in result.steps if s.name == "validate_schema")
        self.assertEqual(validate_step.status, StepStatus.SUCCESS)
        self.assertIsNotNone(result.impact)
        decision_step = next(s for s in result.steps if s.name == "decision")
        self.assertEqual(decision_step.status, StepStatus.SUCCESS)

    def test_full_report_includes_previous_context_section_when_present(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_entities":
                return [
                    {
                        "properties": {
                            "customProperties": [
                                {"key": "changeguard_decision", "value": "BLOCK"},
                                {"key": "changeguard_risk_score", "value": "60"},
                                {"key": "changeguard_severity", "value": "high"},
                                {"key": "changeguard_operation", "value": "drop"},
                                {"key": "changeguard_column", "value": "customer_id"},
                                {"key": "changeguard_timestamp", "value": "2026-08-09T02:40:44+00:00"},
                            ]
                        }
                    }
                ]
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(Change("commerce.orders", "customer_id", "rename", "cust_key"))

        self.assertIn("Previous ChangeGuard Context", result.report)
        self.assertIn("BLOCK", result.report)
        self.assertIn("customer_id", result.report)

    def test_full_report_notes_absence_when_no_previous_context(self):
        async def caller(name, arguments):
            if name == "search":
                return {
                    "searchResults": [
                        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)"}}
                    ]
                }
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_entities":
                return [{"properties": {"customProperties": []}}]
            if name == "get_lineage":
                return {"downstreams": {"searchResults": []}}
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(Change("commerce.orders", "customer_id", "rename", "cust_key"))

        self.assertIn("Previous ChangeGuard Context", result.report)
        self.assertIn("No previously persisted ChangeGuard decision", result.report)

    def test_demo_mode_skips_schema_validation(self):
        """Demo mode has no live schema to check against and must keep
        its existing behavior (no MCP calls at all)."""
        agent = ChangeGuardAgent()
        result = agent.run(Change("commerce.orders", "customer_id", "rename"))

        validate_step = next(s for s in result.steps if s.name == "validate_schema")
        self.assertEqual(validate_step.status, StepStatus.SKIPPED)
        self.assertIsNotNone(result.impact)


if __name__ == "__main__":
    unittest.main()
