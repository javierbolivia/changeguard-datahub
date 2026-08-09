"""Tests for the ChangeGuard CI/CD gate CLI (contract_sentinel/cli.py).

These tests run the CLI's own main() function directly (in-process) so
they exercise the real argument parsing, exit-code mapping, and output
formatting, without needing a live DataHub instance. Live-mode paths are
exercised by monkeypatching ``create_live_adapter`` with a fake adapter
built on the same ``DataHubMCPAdapter`` used everywhere else in this
codebase — this is the same technique ``tests/test_agent.py`` already
uses for its ``LiveModeShapeTests``, so no parallel test harness is
introduced.
"""

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from contract_sentinel import cli
from contract_sentinel.datahub_mcp import DataHubMCPAdapter

TOOLS = {
    "search",
    "get_entities",
    "list_schema_fields",
    "get_lineage",
    "get_lineage_paths_between",
}


def _make_live_caller():
    """A fake MCP tool caller with the real mcp-server-datahub response
    shapes: commerce.orders.customer_id has one confirmed column-level
    downstream (analytics.customer_orders) and one additional table-level
    -only downstream (analytics.sales_summary), matching the seeded local
    DataHub instance used throughout this project's other tests/examples.
    """

    async def caller(name, arguments):
        if name == "search":
            if "this_does_not_exist" in arguments.get("query", ""):
                return {"searchResults": []}
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
            if "column" in arguments:
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

    return caller


def _patched_live_adapter():
    """Return an async context manager patch target that returns a fake
    live DataHubMCPAdapter, wired the same way create_live_adapter() would
    wire a real one — no separate/duplicated adapter logic."""

    async def fake_create_live_adapter(datahub_url, datahub_token=None):
        return DataHubMCPAdapter(_make_live_caller(), TOOLS)

    return patch.object(cli, "create_live_adapter", fake_create_live_adapter)


def _run_cli(argv):
    """Run cli.main() capturing stdout, returning (exit_code, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = cli.main(argv)
    return exit_code, buf.getvalue()


class CliExitCodeTests(unittest.TestCase):
    def test_allow_decision_exits_zero(self):
        """rename in demo mode is well below the BLOCK threshold with no
        downstream fixtures beyond the default set -> ALLOW -> exit 0."""
        with _patched_live_adapter():
            exit_code, out = _run_cli(
                [
                    "--dataset",
                    "commerce.orders",
                    "--column",
                    "customer_id",
                    "--operation",
                    "rename",
                    "--new-name",
                    "cust_key",
                    "--mode",
                    "live",
                ]
            )
        self.assertEqual(exit_code, cli.EXIT_ALLOW)
        self.assertIn("Decision: ALLOW", out)

    def test_block_decision_exits_one(self):
        with _patched_live_adapter():
            exit_code, out = _run_cli(
                [
                    "--dataset",
                    "commerce.orders",
                    "--column",
                    "customer_id",
                    "--operation",
                    "drop",
                    "--mode",
                    "live",
                ]
            )
        self.assertEqual(exit_code, cli.EXIT_BLOCK)
        self.assertIn("Decision: BLOCK", out)

    def test_dataset_not_found_exits_two(self):
        with _patched_live_adapter():
            exit_code, out = _run_cli(
                [
                    "--dataset",
                    "commerce.this_does_not_exist",
                    "--column",
                    "customer_id",
                    "--operation",
                    "drop",
                    "--mode",
                    "live",
                ]
            )
        self.assertEqual(exit_code, cli.EXIT_ERROR)
        # Error output must not contain a decision - it never reached one.
        self.assertNotIn("Decision: ALLOW", out)
        self.assertNotIn("Decision: BLOCK", out)

    def test_column_not_found_exits_two(self):
        with _patched_live_adapter():
            exit_code, out = _run_cli(
                [
                    "--dataset",
                    "commerce.orders",
                    "--column",
                    "this_column_does_not_exist",
                    "--operation",
                    "drop",
                    "--mode",
                    "live",
                ]
            )
        self.assertEqual(exit_code, cli.EXIT_ERROR)

    def test_connection_error_exits_two(self):
        async def failing_adapter(datahub_url, datahub_token=None):
            raise ConnectionError("DataHub GMS unreachable")

        with patch.object(cli, "create_live_adapter", failing_adapter):
            exit_code, out = _run_cli(
                [
                    "--dataset",
                    "commerce.orders",
                    "--column",
                    "customer_id",
                    "--operation",
                    "drop",
                    "--mode",
                    "live",
                ]
            )
        self.assertEqual(exit_code, cli.EXIT_ERROR)

    def test_rename_without_new_name_exits_two(self):
        """rename requires --new-name; this is a configuration error, not
        a DataHub error, and must fail before any MCP call is attempted."""
        exit_code, out = _run_cli(
            [
                "--dataset",
                "commerce.orders",
                "--column",
                "customer_id",
                "--operation",
                "rename",
            ]
        )
        self.assertEqual(exit_code, cli.EXIT_ERROR)


class CliJsonOutputTests(unittest.TestCase):
    def test_json_output_is_valid_json(self):
        with _patched_live_adapter():
            exit_code, out = _run_cli(
                [
                    "--dataset",
                    "commerce.orders",
                    "--column",
                    "customer_id",
                    "--operation",
                    "drop",
                    "--mode",
                    "live",
                    "--json",
                ]
            )
        # stdout must be exactly one parseable JSON object, with no other
        # diagnostic text mixed in.
        payload = json.loads(out)
        self.assertIsInstance(payload, dict)

    def test_json_output_contains_correct_decision_and_score(self):
        with _patched_live_adapter():
            exit_code, out = _run_cli(
                [
                    "--dataset",
                    "commerce.orders",
                    "--column",
                    "customer_id",
                    "--operation",
                    "drop",
                    "--mode",
                    "live",
                    "--json",
                ]
            )
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "BLOCK")
        self.assertEqual(payload["risk_score"], 60)
        self.assertEqual(payload["severity"], "high")
        self.assertEqual(payload["confirmed_affected_assets"], 1)
        self.assertEqual(payload["potential_downstream_assets"], 1)
        self.assertEqual(payload["mode"], "live")
        self.assertEqual(exit_code, cli.EXIT_BLOCK)

    def test_json_output_for_allow_case(self):
        with _patched_live_adapter():
            exit_code, out = _run_cli(
                [
                    "--dataset",
                    "commerce.orders",
                    "--column",
                    "customer_id",
                    "--operation",
                    "rename",
                    "--new-name",
                    "cust_key",
                    "--mode",
                    "live",
                    "--json",
                ]
            )
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "ALLOW")
        self.assertEqual(payload["risk_score"], 50)
        self.assertEqual(payload["severity"], "medium")
        self.assertEqual(exit_code, cli.EXIT_ALLOW)


class CliReusesExistingEngineTests(unittest.TestCase):
    """The CLI must reuse ChangeGuardAgent/assess_change, not a parallel
    scoring table. These tests fail if the CLI ever starts computing its
    own score/decision independently of contract_sentinel.risk."""

    def test_cli_module_does_not_import_or_define_its_own_scoring(self):
        import inspect

        source = inspect.getsource(cli)
        # The CLI must not define a competing risk/scoring function.
        self.assertNotIn("def assess_change", source)
        self.assertNotIn("def assess_risk", source)
        # It must import the real scoring entry points it depends on
        # (ChangeGuardAgent internally calls assess_change from risk.py).
        self.assertIn("from .agent import", source)
        self.assertIn("from .risk import Change", source)

    def test_demo_mode_matches_agent_run_directly(self):
        """Running the CLI in demo mode must produce the exact same score
        as calling ChangeGuardAgent directly for the same Change - proof
        the CLI is a thin wrapper, not a second engine."""
        from contract_sentinel.agent import ChangeGuardAgent
        from contract_sentinel.risk import Change

        agent = ChangeGuardAgent()
        direct_result = agent.run(Change("commerce.orders", "customer_id", "drop"))

        exit_code, out = _run_cli(
            [
                "--dataset",
                "commerce.orders",
                "--column",
                "customer_id",
                "--operation",
                "drop",
                "--mode",
                "demo",
                "--json",
            ]
        )
        payload = json.loads(out)
        self.assertEqual(payload["risk_score"], direct_result.impact.score)
        self.assertEqual(payload["severity"], direct_result.impact.severity)


if __name__ == "__main__":
    unittest.main()
