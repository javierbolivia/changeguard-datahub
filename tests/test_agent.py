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

    def test_potential_downstream_failure_does_not_block_pipeline(self):
        """fetch_potential_downstream is informational only - a failure
        there must not prevent risk scoring or the final decision."""

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
                    return {"downstreams": {"searchResults": []}}
                raise ConnectionError("table-level lineage unavailable")
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = self._make_adapter(caller)
        agent = ChangeGuardAgent(mcp_adapter=adapter)
        result = agent.run(Change("commerce.orders", "customer_id", "rename"))

        potential_step = next(s for s in result.steps if s.name == "fetch_potential_downstream")
        self.assertEqual(potential_step.status, StepStatus.FAILED)
        # Pipeline continues: risk assessment and decision still complete.
        self.assertIsNotNone(result.impact)
        decision_step = next(s for s in result.steps if s.name == "decision")
        self.assertEqual(decision_step.status, StepStatus.SUCCESS)

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
        # Different operation from the persisted one (drop) - rename here.
        result = agent.run(Change("commerce.orders", "customer_id", "rename", "cust_key"))

        self.assertIsNotNone(result.previous_context)
        self.assertEqual(result.previous_context.decision, "BLOCK")
        self.assertEqual(result.previous_context.risk_score, 60)
        self.assertEqual(result.previous_context.operation, "drop")

        # The CURRENT analysis must be unaffected by the persisted BLOCK:
        # a rename with no downstream assets in this scenario is a fresh,
        # independently computed score, not a copy of the old one.
        self.assertNotEqual(result.impact.score, 60)
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
