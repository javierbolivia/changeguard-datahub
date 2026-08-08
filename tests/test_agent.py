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
        self.assertEqual(len(result.steps), 7)
        self.assertIsNotNone(result.impact)
        self.assertIsNotNone(result.report)
        self.assertEqual(result.impact.severity, "critical")
        self.assertGreaterEqual(result.impact.score, 80)

    def test_agent_emits_step_callbacks(self):
        events = []
        agent = ChangeGuardAgent(on_step_update=lambda s: events.append(s.status))
        agent.run(Change("commerce.orders", "customer_id", "drop"))

        # Each step emits RUNNING + final status = at least 14 events
        self.assertGreaterEqual(len(events), 14)
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
                return {"searchResults": []}
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
                return {"searchResults": []}
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


if __name__ == "__main__":
    unittest.main()
