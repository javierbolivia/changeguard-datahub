"""Tests for the DataHub persistent writeback helper."""

import unittest

from contract_sentinel.datahub_writeback import build_writeback_properties


class WritebackPropertiesTests(unittest.TestCase):
    def test_builds_all_expected_keys(self):
        props = build_writeback_properties(
            decision="BLOCK",
            risk_score=60,
            severity="high",
            operation="drop",
            column="customer_id",
            timestamp="2026-08-08T12:00:00+00:00",
        )

        self.assertEqual(props["changeguard_decision"], "BLOCK")
        self.assertEqual(props["changeguard_risk_score"], "60")
        self.assertEqual(props["changeguard_severity"], "high")
        self.assertEqual(props["changeguard_operation"], "drop")
        self.assertEqual(props["changeguard_column"], "customer_id")
        self.assertEqual(props["changeguard_timestamp"], "2026-08-08T12:00:00+00:00")

    def test_all_values_are_strings(self):
        """DataHub custom properties are string-typed; risk_score (an int)
        must be converted, not passed through as a number."""
        props = build_writeback_properties(
            decision="ALLOW",
            risk_score=50,
            severity="medium",
            operation="rename",
            column="customer_id",
        )
        for value in props.values():
            self.assertIsInstance(value, str)

    def test_generates_timestamp_when_not_provided(self):
        props = build_writeback_properties(
            decision="ALLOW",
            risk_score=5,
            severity="low",
            operation="add",
            column="notes",
        )
        self.assertTrue(props["changeguard_timestamp"])
        # ISO 8601 with a 'T' separator and timezone info.
        self.assertIn("T", props["changeguard_timestamp"])


if __name__ == "__main__":
    unittest.main()

class ParsePersistedContextTests(unittest.TestCase):
    """Tests for reading DataHub Memory back out of a real get_entities
    response shape, as observed against a live mcp-server-datahub server."""

    REAL_RESPONSE = [
        {
            "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,commerce.orders,PROD)",
            "name": "commerce.orders",
            "platform": {"urn": "urn:li:dataPlatform:snowflake", "name": "snowflake"},
            "properties": {
                "name": "commerce.orders",
                "description": "Real seeded dataset for ChangeGuard Live verification.",
                "customProperties": [
                    {"key": "changeguard_risk_score", "value": "60"},
                    {"key": "changeguard_decision", "value": "BLOCK"},
                    {"key": "changeguard_timestamp", "value": "2026-08-09T02:40:44+00:00"},
                    {"key": "changeguard_severity", "value": "high"},
                    {"key": "changeguard_operation", "value": "drop"},
                    {"key": "changeguard_column", "value": "customer_id"},
                ],
            },
            "health": [{"type": "INCIDENTS", "status": "PASS"}],
            "schemaMetadata": {"name": "commerce.orders", "platformUrn": "urn:li:dataPlatform:snowflake", "fields": []},
            "relatedDocuments": {"start": 0, "count": 10, "total": 0},
        }
    ]

    def test_parses_real_response_shape_correctly(self):
        from contract_sentinel.datahub_writeback import parse_persisted_context

        context = parse_persisted_context(self.REAL_RESPONSE)

        self.assertIsNotNone(context)
        self.assertEqual(context.decision, "BLOCK")
        self.assertEqual(context.risk_score, 60)
        self.assertEqual(context.severity, "high")
        self.assertEqual(context.operation, "drop")
        self.assertEqual(context.column, "customer_id")
        self.assertEqual(context.timestamp, "2026-08-09T02:40:44+00:00")

    def test_no_changeguard_properties_returns_none(self):
        """A dataset with no changeguard_* custom properties (never
        persisted to) must return None, not raise or invent defaults."""
        from contract_sentinel.datahub_writeback import parse_persisted_context

        response = [
            {
                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.sales_summary,PROD)",
                "properties": {"name": "analytics.sales_summary", "customProperties": []},
            }
        ]
        self.assertIsNone(parse_persisted_context(response))

    def test_missing_properties_key_returns_none(self):
        from contract_sentinel.datahub_writeback import parse_persisted_context

        response = [{"urn": "urn:li:dataset:(x,y,PROD)"}]
        self.assertIsNone(parse_persisted_context(response))

    def test_empty_entity_list_returns_none(self):
        from contract_sentinel.datahub_writeback import parse_persisted_context

        self.assertIsNone(parse_persisted_context([]))

    def test_non_list_response_returns_none_without_crashing(self):
        from contract_sentinel.datahub_writeback import parse_persisted_context

        self.assertIsNone(parse_persisted_context(None))
        self.assertIsNone(parse_persisted_context({}))

    def test_unparseable_risk_score_degrades_gracefully(self):
        """A malformed changeguard_risk_score must not crash parsing - the
        rest of the persisted context is still useful."""
        from contract_sentinel.datahub_writeback import parse_persisted_context

        response = [
            {
                "properties": {
                    "customProperties": [
                        {"key": "changeguard_decision", "value": "BLOCK"},
                        {"key": "changeguard_risk_score", "value": "not-a-number"},
                    ]
                }
            }
        ]
        context = parse_persisted_context(response)
        self.assertIsNotNone(context)
        self.assertEqual(context.decision, "BLOCK")
        self.assertIsNone(context.risk_score)

    def test_partial_properties_fill_unknown_for_missing_fields(self):
        """Incomplete changeguard_* properties (e.g. only decision was
        ever written) must not crash - missing fields become 'Unknown'."""
        from contract_sentinel.datahub_writeback import parse_persisted_context

        response = [
            {
                "properties": {
                    "customProperties": [
                        {"key": "changeguard_decision", "value": "ALLOW"},
                    ]
                }
            }
        ]
        context = parse_persisted_context(response)
        self.assertIsNotNone(context)
        self.assertEqual(context.decision, "ALLOW")
        self.assertEqual(context.severity, "Unknown")
        self.assertEqual(context.operation, "Unknown")
        self.assertEqual(context.column, "Unknown")
        self.assertEqual(context.timestamp, "Unknown")
