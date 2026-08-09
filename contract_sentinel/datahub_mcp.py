"""Small, testable boundary around the official DataHub MCP tools.

The transport client is injected so the domain logic does not depend on a
specific agent framework. In production, ``call_tool`` is backed by an MCP
stdio client connected to the official ``mcp-server-datahub`` package
(https://github.com/acryldata/mcp-server-datahub), launched via
``uvx mcp-server-datahub@latest``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


ToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Closer = Callable[[], Awaitable[None]]


class DataHubMCPAdapter:
    # Tools required for the core ChangeGuard analysis flow (search,
    # lineage, schema/entity inspection). Without these the agent cannot
    # discover datasets or trace impact, so connecting must fail fast.
    REQUIRED_TOOLS = {
        "search",
        "get_entities",
        "list_schema_fields",
        "get_lineage",
        "get_lineage_paths_between",
    }

    # Tools that enhance the flow but are not required to analyze a change
    # and produce a BLOCK/ALLOW/REVIEW decision. ``save_document`` is a Document
    # Tool (not a Mutation Tool) and mcp-server-datahub automatically hides
    # it when no documents exist yet in the catalog, so its absence must
    # not prevent connecting in Live mode.
    OPTIONAL_TOOLS = {
        "save_document",
    }

    def __init__(
        self,
        call_tool: ToolCaller,
        available_tools: set[str],
        close: Closer | None = None,
    ) -> None:
        missing = self.REQUIRED_TOOLS - available_tools
        if missing:
            raise RuntimeError(f"DataHub MCP server is missing tools: {sorted(missing)}")
        self._call_tool = call_tool
        self._close = close
        self.save_document_available = "save_document" in available_tools

    async def close(self) -> None:
        """Release the underlying transport (e.g. terminate the MCP subprocess)."""
        if self._close:
            await self._close()

    async def downstream_lineage(self, urn: str, column: str) -> dict[str, Any]:
        """Column-level downstream lineage.

        Only returns entities for which DataHub has a confirmed
        column-level lineage relationship with ``column``. This is the
        source of truth for CONFIRMED impact and risk scoring.
        """
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

    async def downstream_lineage_table_level(self, urn: str) -> dict[str, Any]:
        """Table-level downstream lineage (no ``column`` filter).

        DataHub's column-level query only returns entities with a proven
        column-to-column dependency. This broader, table-level query can
        surface additional downstream datasets that are not (yet) known
        to depend on the specific column — these must be treated as
        POTENTIAL downstream propagation, not confirmed impact, and must
        never be counted in risk scoring.
        """
        return await self._call_tool(
            "get_lineage",
            {
                "urn": urn,
                "upstream": False,
                "max_hops": 3,
                "max_results": 30,
            },
        )

    async def get_persisted_context(self, urn: str) -> Any:
        """Read entity metadata for ``urn`` via ``get_entities``.

        This is how ChangeGuard reads back its own previously persisted
        decision: ``get_entities`` returns (among other fields) the
        dataset's ``properties.customProperties``, which is exactly what
        ``datahub_writeback.write_decision_to_datahub`` writes onto the
        dataset (``changeguard_decision``, ``changeguard_risk_score``,
        etc.) via the DataHub SDK.

        Note: as observed against the real ``mcp-server-datahub`` server,
        ``get_entities`` does not include ``ownership``, ``domain``, or
        ``tags`` in its response shape today — only ``platform``,
        ``properties`` (incl. ``customProperties``), ``health``,
        ``schemaMetadata``, and ``relatedDocuments``. This method is
        therefore scoped to reading persisted ChangeGuard context and the
        entity metadata ``get_entities`` actually provides, not to
        ownership/domain/tags enrichment.
        """
        return await self._call_tool("get_entities", {"urns": [urn]})

    async def save_impact_report(
        self, title: str, content: str, related_assets: list[str], confirmed: bool
    ) -> dict[str, Any]:
        if not confirmed:
            raise PermissionError("DataHub writeback requires explicit confirmation.")
        if not self.save_document_available:
            raise RuntimeError(
                "save_document unavailable: this DataHub MCP server did not "
                "advertise the save_document tool."
            )
        return await self._call_tool(
            "save_document",
            {
                "document_type": "Analysis",
                "title": title,
                "content": content,
                "related_assets": related_assets,
            },
        )
