"""Tests for the ChangeGuard autonomous agent."""

import unittest

from contract_sentinel.agent import (
    AgentResult,
    AgentStep,
    ChangeGuardAgent,
    StepStatus,
)
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


if __name__ == "__main__":
    unittest.main()
