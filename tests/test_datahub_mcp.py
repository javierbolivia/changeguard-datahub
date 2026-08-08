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

    def test_save_document_is_optional_for_connecting(self):
        """save_document is a Document Tool that mcp-server-datahub hides
        when no documents exist yet. Its absence must not block connecting,
        since it is not required for the core analysis flow."""

        async def caller(name, arguments):
            return {}

        tools_without_save_document = TOOLS - {"save_document"}
        adapter = DataHubMCPAdapter(caller, tools_without_save_document)
        self.assertFalse(adapter.save_document_available)

    def test_save_document_available_when_tool_present(self):
        async def caller(name, arguments):
            return {"success": True}

        adapter = DataHubMCPAdapter(caller, TOOLS)
        self.assertTrue(adapter.save_document_available)

    def test_save_impact_report_fails_clearly_when_unavailable(self):
        async def caller(name, arguments):
            return {}

        tools_without_save_document = TOOLS - {"save_document"}
        adapter = DataHubMCPAdapter(caller, tools_without_save_document)
        with self.assertRaises(RuntimeError):
            asyncio.run(
                adapter.save_impact_report("title", "content", [], confirmed=True)
            )


if __name__ == "__main__":
    unittest.main()
