import unittest

from contract_sentinel.fixtures import SHOWCASE_ASSETS
from contract_sentinel.report import render_markdown
from contract_sentinel.risk import Change, assess_change


class ReportTests(unittest.TestCase):
    def test_report_contains_decision_lineage_and_owner(self):
        change = Change("commerce.orders", "customer_id", "rename")
        impact = assess_change(change, SHOWCASE_ASSETS)
        report = render_markdown(change, impact, SHOWCASE_ASSETS)
        self.assertIn("90/100", report)
        self.assertIn("Revenue Executive Dashboard", report)
        self.assertIn("Finance Analytics", report)
        self.assertIn("Migration checklist", report)


if __name__ == "__main__":
    unittest.main()
