"""Small, testable boundary around the official DataHub MCP tools.

The transport client is injected so the domain logic does not depend on a
specific agent framework. In production, ``call_tool`` is backed by an MCP
stdio client connected to ``mcp-server-datahub@0.6.0``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


ToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class DataHubMCPAdapter:
    REQUIRED_TOOLS = {
        "search",
        "get_entities",
        "list_schema_fields",
        "get_lineage",
        "get_lineage_paths_between",
        "save_document",
    }

    def __init__(self, call_tool: ToolCaller, available_tools: set[str]) -> None:
        missing = self.REQUIRED_TOOLS - available_tools
        if missing:
            raise RuntimeError(f"DataHub MCP server is missing tools: {sorted(missing)}")
        self._call_tool = call_tool

    async def downstream_lineage(self, urn: str, column: str) -> dict[str, Any]:
        return await self._call_tool(
            "get_lineage",
            {
                "urn": urn,
                "column": column,
                "upstream": False,
                "max_hops": 3,
                "max_results": 30,
            },
        )

    async def save_impact_report(
        self, title: str, content: str, related_assets: list[str], confirmed: bool
    ) -> dict[str, Any]:
        if not confirmed:
            raise PermissionError("DataHub writeback requires explicit confirmation.")
        return await self._call_tool(
            "save_document",
            {
                "document_type": "Analysis",
                "title": title,
                "content": content,
                "related_assets": related_assets,
            },
        )

