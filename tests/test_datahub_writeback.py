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
