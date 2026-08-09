"""Focused tests for deterministic remediation guidance."""

import unittest

from contract_sentinel.agent import ChangeGuardAgent
from contract_sentinel.datahub_mcp import DataHubMCPAdapter
from contract_sentinel.remediation import build_remediation_plan
from contract_sentinel.risk import Change, assess_change


CONFIRMED = [
    {
        "name": "analytics.customer_orders",
        "urn": "urn:confirmed",
        "kind": "dataset",
        "critical": False,
    }
]
POTENTIAL = [
    {
        "name": "analytics.sales_summary",
        "urn": "urn:potential",
        "kind": "dataset",
    }
]


class RemediationRuleTests(unittest.TestCase):
    def test_drop_with_confirmed_consumer_requires_remediation(self):
        plan = build_remediation_plan(
            Change("commerce.orders", "customer_id", "drop"),
            "high",
            "BLOCK",
            CONFIRMED,
            POTENTIAL,
        )

        self.assertTrue(plan.required)
        self.assertIn("Recommended path to re-evaluation", plan.summary)
        self.assertEqual(
            [step.kind for step in plan.steps],
            [
                "confirmed_migration",
                "potential_review",
                "compatibility_transition",
                "re_evaluate",
            ],
        )

    def test_drop_remediation_names_confirmed_consumer(self):
        plan = build_remediation_plan(
            Change("commerce.orders", "customer_id", "drop"),
            "high",
            "BLOCK",
            CONFIRMED,
            POTENTIAL,
        )

        confirmed_step = next(
            step for step in plan.steps if step.kind == "confirmed_migration"
        )
        self.assertEqual(confirmed_step.assets, ("analytics.customer_orders",))
        self.assertIn("analytics.customer_orders", confirmed_step.detail)

    def test_potential_asset_is_review_only_and_not_confirmed(self):
        plan = build_remediation_plan(
            Change("commerce.orders", "customer_id", "drop"),
            "high",
            "BLOCK",
            CONFIRMED,
            POTENTIAL,
        )

        potential_step = next(
            step for step in plan.steps if step.kind == "potential_review"
        )
        self.assertEqual(potential_step.assets, ("analytics.sales_summary",))
        self.assertIn("column-level impact is not confirmed", potential_step.detail)
        self.assertNotIn("analytics.sales_summary", plan.steps[0].assets)

    def test_potential_assets_do_not_change_drop_score(self):
        change = Change("commerce.orders", "customer_id", "drop")
        impact_before = assess_change(change, CONFIRMED)
        build_remediation_plan(
            change, impact_before.severity, "BLOCK", CONFIRMED, POTENTIAL
        )
        impact_after = assess_change(change, CONFIRMED)

        self.assertEqual(impact_before, impact_after)
        self.assertEqual(impact_after.score, 60)

    def test_rename_guidance_differs_from_drop_and_remains_allow(self):
        rename = build_remediation_plan(
            Change("commerce.orders", "customer_id", "rename", "cust_key"),
            "medium",
            "ALLOW",
            CONFIRMED,
            POTENTIAL,
        )
        drop = build_remediation_plan(
            Change("commerce.orders", "customer_id", "drop"),
            "high",
            "BLOCK",
            CONFIRMED,
            POTENTIAL,
        )

        self.assertFalse(rename.required)
        self.assertNotEqual(rename.steps, drop.steps)
        self.assertEqual(rename.steps[0].kind, "confirmed_reference_update")
        self.assertIn("cust_key", rename.steps[0].detail)
        self.assertNotIn("blocked", rename.summary.casefold())

    def test_add_has_no_destructive_migration_instruction(self):
        plan = build_remediation_plan(
            Change("commerce.orders", "new_note", "add", "string"),
            "low",
            "ALLOW",
            CONFIRMED,
            POTENTIAL,
        )
        all_guidance = " ".join(step.detail.casefold() for step in plan.steps)

        self.assertFalse(plan.required)
        self.assertEqual(plan.steps[0].kind, "additive_validation")
        self.assertNotIn("before removing", all_guidance)
        self.assertNotIn("migrate or update", all_guidance)

    def test_type_change_reviews_incompatible_confirmed_consumers(self):
        plan = build_remediation_plan(
            Change("commerce.orders", "amount", "type_change", "decimal(18,2)"),
            "medium",
            "ALLOW",
            CONFIRMED,
            POTENTIAL,
        )

        self.assertEqual(plan.steps[0].kind, "confirmed_compatibility_review")
        self.assertIn("decimal(18,2)", plan.steps[0].detail)
        self.assertIn("analytics.customer_orders", plan.steps[0].detail)

    def test_same_input_produces_same_ordered_plan(self):
        confirmed = CONFIRMED + [{"name": "analytics.a_first", "urn": "urn:a"}]
        potential = POTENTIAL + [{"name": "analytics.z_last", "urn": "urn:z"}]
        change = Change("commerce.orders", "customer_id", "drop")

        first = build_remediation_plan(
            change, "high", "BLOCK", confirmed, potential
        )
        second = build_remediation_plan(
            change,
            "high",
            "BLOCK",
            list(reversed(confirmed)),
            list(reversed(potential)),
        )
        self.assertEqual(first, second)

    def test_missing_potential_assets_is_clean(self):
        plan = build_remediation_plan(
            Change("commerce.orders", "customer_id", "rename", "cust_key"),
            "medium",
            "ALLOW",
            CONFIRMED,
            [],
        )
        self.assertNotIn("potential_review", [step.kind for step in plan.steps])

    def test_missing_confirmed_assets_is_clean(self):
        plan = build_remediation_plan(
            Change("commerce.orders", "customer_id", "drop"),
            "high",
            "BLOCK",
            [],
            POTENTIAL,
        )
        self.assertEqual(plan.steps[0].kind, "confirmed_validation")
        self.assertEqual(plan.steps[0].assets, ())
        self.assertIn("No confirmed", plan.steps[0].detail)

    def test_low_allow_without_lineage_has_no_blocking_remediation(self):
        plan = build_remediation_plan(
            Change("commerce.orders", "new_note", "add"),
            "low",
            "ALLOW",
            [],
            [],
        )
        self.assertFalse(plan.required)
        self.assertEqual(plan.summary, "No blocking remediation required.")
        self.assertNotIn("blocked", plan.summary.casefold())


class RemediationAgentIntegrationTests(unittest.TestCase):
    TOOLS = {
        "search",
        "get_entities",
        "list_schema_fields",
        "get_lineage",
        "get_lineage_paths_between",
    }

    def _run(self, previous_context):
        async def caller(name, arguments):
            if name == "search":
                return {"searchResults": [{"entity": {"urn": "urn:orders"}}]}
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_entities":
                return previous_context
            if name == "get_lineage" and "column" in arguments:
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
            if name == "get_lineage":
                return {
                    "downstreams": {
                        "searchResults": [
                            {
                                "entity": {
                                    "urn": "urn:confirmed",
                                    "name": "analytics.customer_orders",
                                }
                            },
                            {
                                "entity": {
                                    "urn": "urn:potential",
                                    "name": "analytics.sales_summary",
                                }
                            },
                        ]
                    }
                }
            raise AssertionError(f"unexpected tool call: {name}")

        adapter = DataHubMCPAdapter(caller, self.TOOLS)
        return ChangeGuardAgent(mcp_adapter=adapter).run(
            Change("commerce.orders", "customer_id", "drop")
        )

    def test_previous_datahub_context_does_not_change_remediation(self):
        without_previous = self._run([])
        with_previous = self._run(
            [
                {
                    "properties": {
                        "customProperties": [
                            {"key": "changeguard_decision", "value": "ALLOW"},
                            {"key": "changeguard_risk_score", "value": "5"},
                            {"key": "changeguard_severity", "value": "low"},
                            {"key": "changeguard_operation", "value": "add"},
                            {"key": "changeguard_column", "value": "other"},
                            {"key": "changeguard_timestamp", "value": "earlier"},
                        ]
                    }
                }
            ]
        )

        self.assertEqual(without_previous.remediation, with_previous.remediation)
        self.assertEqual(with_previous.impact.score, 60)

    def test_report_renders_the_same_structured_plan(self):
        result = self._run([])
        self.assertIn("## Remediation Plan", result.report)
        for action in result.remediation.steps:
            self.assertIn(action.title, result.report)
            self.assertIn(action.detail, result.report)

    def test_verified_drop_and_rename_scores_remain_exact(self):
        drop = self._run([])
        self.assertEqual((drop.impact.score, drop.impact.severity), (60, "high"))
        drop_decision = next(s for s in drop.steps if s.name == "decision")
        self.assertEqual(drop_decision.result["decision"], "BLOCK")

        async def caller(name, arguments):
            if name == "search":
                return {"searchResults": [{"entity": {"urn": "urn:orders"}}]}
            if name == "list_schema_fields":
                return {"fields": [{"fieldPath": "customer_id"}]}
            if name == "get_entities":
                return []
            if name == "get_lineage" and "column" in arguments:
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
            if name == "get_lineage":
                return {
                    "downstreams": {
                        "searchResults": [
                            {
                                "entity": {
                                    "urn": "urn:confirmed",
                                    "name": "analytics.customer_orders",
                                }
                            },
                            {
                                "entity": {
                                    "urn": "urn:potential",
                                    "name": "analytics.sales_summary",
                                }
                            },
                        ]
                    }
                }
            raise AssertionError(f"unexpected tool call: {name}")

        rename = ChangeGuardAgent(
            mcp_adapter=DataHubMCPAdapter(caller, self.TOOLS)
        ).run(Change("commerce.orders", "customer_id", "rename", "cust_key"))
        self.assertEqual((rename.impact.score, rename.impact.severity), (50, "medium"))
        rename_decision = next(s for s in rename.steps if s.name == "decision")
        self.assertEqual(rename_decision.result["decision"], "ALLOW")


if __name__ == "__main__":
    unittest.main()
