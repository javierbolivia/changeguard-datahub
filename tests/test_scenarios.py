"""Tests for truthful Streamlit quick-scenario presets."""

import unittest

from contract_sentinel.scenarios import SAFE_RENAME, streamlit_state_for


class QuickScenarioTests(unittest.TestCase):
    def test_safe_rename_loads_the_verified_live_allow_scenario(self):
        state = streamlit_state_for(SAFE_RENAME)

        self.assertEqual(state["cg_dataset"], "commerce.orders")
        self.assertEqual(state["cg_column"], "customer_id")
        self.assertEqual(state["cg_operation"], "rename")
        self.assertEqual(state["cg_new_value"], "cust_key")
        self.assertEqual(state["cg_mode"], "Live (DataHub MCP)")


if __name__ == "__main__":
    unittest.main()
