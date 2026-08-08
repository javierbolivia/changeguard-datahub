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
        self.assertEqual(len(result.steps), 9)
        self.assertIsNotNone(result.impact)
        self.assertIsNotNone(result.report)
        self.assertEqual(result.impact.severity, "critical")
        self.assertGreaterEqual(result.impact.score, 80)

    def test_agent_emits_step_callbacks(self):
        events = []
        agent = ChangeGuardAgent(on_step_update=lambda s: events.append(s.status))
        agent.run(Change("commerce.orders", "customer_id", "drop"))

        # Each step emits RUNNING + final status = at least 18 events (9 steps)
        self.assertGreaterEqual(len(events), 18)
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

        def fake_writeback(dataset, decision, score, severity, operation, column):
            calls.append((dataset, decision, score, severity, operation, column))
            return {"dataset_urn": f"urn:li:dataset:(x,{dataset},PROD)"}

        agent = ChangeGuardAgent(writeback_fn=fake_writeback)
        result = agent.run(
            Change("commerce.orders", "customer_id", "drop"),
            confirm_writeback=True,
        )

        persist_step = next(s for s in result.steps if s.name == "persist_decision")
        self.assertEqual(persist_step.status, StepStatus.SUCCESS)
        self.assertTrue(result.writeback_success)
        self.assertEqual(len(calls), 1)
        dataset, decision, score, severity, operation, column = calls[0]
        self.assertEqual(dataset, "commerce.orders")
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

    def test_resolve_urn_parses_real_search_shape(self):
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
