import asyncio
import unittest

from contract_sentinel.datahub_mcp import DataHubMCPAdapter


TOOLS = {
    "search",
    "get_entities",
    "list_schema_fields",
    "get_lineage",
    "get_lineage_paths_between",
    "save_document",
}


class AdapterTests(unittest.TestCase):
    def test_lineage_uses_column_level_downstream_query(self):
        calls = []

        async def caller(name, arguments):
            calls.append((name, arguments))
            return {"results": []}

        adapter = DataHubMCPAdapter(caller, TOOLS)
        asyncio.run(adapter.downstream_lineage("urn:orders", "customer_id"))
        self.assertEqual(calls[0][0], "get_lineage")
        self.assertFalse(calls[0][1]["upstream"])
        self.assertEqual(calls[0][1]["max_hops"], 3)

    def test_writeback_is_blocked_without_confirmation(self):
        async def caller(name, arguments):
            return {"success": True}

        adapter = DataHubMCPAdapter(caller, TOOLS)
        with self.assertRaises(PermissionError):
            asyncio.run(adapter.save_impact_report("report", "content", [], False))

    def test_missing_official_tool_fails_fast(self):
        async def caller(name, arguments):
            return {}

        with self.assertRaises(RuntimeError):
            DataHubMCPAdapter(caller, {"search"})


if __name__ == "__main__":
    unittest.main()
