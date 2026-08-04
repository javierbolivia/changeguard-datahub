import unittest

from contract_sentinel import Change, assess_change


class RiskTests(unittest.TestCase):
    def test_drop_with_critical_dependencies_is_critical(self):
        result = assess_change(
            Change("orders", "customer_id", "drop"),
            [
                {"name": "revenue", "kind": "dashboard", "critical": True},
                {"name": "retention", "kind": "dataset", "critical": True},
            ],
        )
        self.assertEqual(result.severity, "critical")
        self.assertGreaterEqual(result.score, 80)

    def test_add_without_dependencies_is_low(self):
        result = assess_change(Change("orders", "note", "add"), [])
        self.assertEqual(result.severity, "low")
        self.assertEqual(result.score, 5)


if __name__ == "__main__":
    unittest.main()
