"""Live MCP connection handler for DataHub.

This module manages the actual connection to the official, open-source
DataHub MCP server (https://github.com/acryldata/mcp-server-datahub),
launched via ``uvx`` over stdio. It creates a real ToolCaller that the
agent uses to communicate with a running DataHub instance.

Requires:
    - ``uv``/``uvx`` installed (https://docs.astral.sh/uv/getting-started/installation/)
    - A running DataHub instance (e.g. ``datahub docker quickstart``)
    - A DataHub personal access token, if the instance has authentication
      enabled. The default local quickstart runs with authentication
      disabled, so ``datahub_token`` may be omitted for local development.

Usage:
    from contract_sentinel.mcp_connection import create_live_adapter

    adapter = await create_live_adapter(
        datahub_url="http://localhost:8080",
        datahub_token=None,  # or a personal access token
    )
    agent = ChangeGuardAgent(mcp_adapter=adapter)
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from .datahub_mcp import DataHubMCPAdapter, ToolCaller


class MCPStdioClient:
    """Minimal MCP stdio client for communicating with mcp-server-datahub.

    Implements the MCP protocol over stdin/stdout to communicate with the
    official, open-source DataHub MCP server
    (https://github.com/acryldata/mcp-server-datahub), launched via
    ``uvx mcp-server-datahub@latest``.
    """

    def __init__(self, process: asyncio.subprocess.Process):
        self._process = process
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._tools: set[str] = set()

    @classmethod
    async def connect(
        cls,
        datahub_url: str = "http://localhost:8080",
        datahub_token: str | None = None,
    ) -> "MCPStdioClient":
        """Start the official mcp-server-datahub process and connect to it.

        Launches ``uvx mcp-server-datahub@latest`` with the DataHub
        connection details passed via the environment variables the
        server expects: ``DATAHUB_GMS_URL`` and ``DATAHUB_GMS_TOKEN``.
        """
        env = {
            **os.environ,
            "DATAHUB_GMS_URL": datahub_url,
        }
        if datahub_token:
            env["DATAHUB_GMS_TOKEN"] = datahub_token

        # Start the official DataHub MCP server via uvx
        process = await asyncio.create_subprocess_exec(
            "uvx",
            "mcp-server-datahub@latest",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        client = cls(process)
        client._reader_task = asyncio.create_task(client._read_responses())

        # Initialize the connection
        await client._initialize()
        return client

    async def _initialize(self) -> None:
        """Perform the MCP initialization handshake and discover tools.

        Per the MCP spec, the client must: send ``initialize``, wait for the
        server's response, then send the ``notifications/initialized``
        notification before issuing any other request (e.g. ``tools/list``).
        """
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "changeguard", "version": "1.0.0"},
        })

        # Required notification with no "id" and no response expected.
        await self._send_notification("notifications/initialized", {})

        # List available tools
        tools_response = await self._send_request("tools/list", {})
        for tool in tools_response.get("tools", []):
            self._tools.add(tool["name"])

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        line = json.dumps(message) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and wait for the response."""
        self._request_id += 1
        request_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        line = json.dumps(message) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        # Wait for response
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise TimeoutError(f"MCP request '{method}' timed out after 30s")

    async def _read_responses(self) -> None:
        """Background task to read responses from the MCP server."""
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break

                try:
                    message = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue

                request_id = message.get("id")
                if request_id and request_id in self._pending:
                    future = self._pending.pop(request_id)
                    if "error" in message:
                        future.set_exception(
                            RuntimeError(
                                f"MCP error: {message['error'].get('message', 'Unknown')}"
                            )
                        )
                    else:
                        future.set_result(message.get("result", {}))
        except asyncio.CancelledError:
            pass

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call an MCP tool and return the result."""
        response = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        # Parse the content from MCP response format
        content = response.get("content", [])
        if content and isinstance(content, list):
            first = content[0]
            if first.get("type") == "text":
                try:
                    return json.loads(first["text"])
                except (json.JSONDecodeError, KeyError):
                    return {"raw": first.get("text", "")}
        return response

    @property
    def available_tools(self) -> set[str]:
        return self._tools

    async def close(self) -> None:
        """Shut down the MCP server process.

        On Windows, ``Process.wait()`` can hang indefinitely after
        ``terminate()`` if the stdout-reading task was only cancelled
        (not awaited) beforehand. To guarantee this method always
        returns, the reader task is awaited (swallowing the expected
        CancelledError) before terminating, and both ``terminate()`` and
        the final ``wait()`` are bounded by a timeout with a ``kill()``
        fallback.
        """
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await asyncio.wait_for(self._reader_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if self._process.stdin:
            self._process.stdin.close()

        try:
            self._process.terminate()
        except ProcessLookupError:
            pass  # Already exited.

        try:
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass  # Give up waiting; the process was at least signalled.


async def create_live_adapter(
    datahub_url: str = "http://localhost:8080",
    datahub_token: str | None = None,
) -> DataHubMCPAdapter:
    """Create a DataHubMCPAdapter connected to a live MCP server.

    Args:
        datahub_url: DataHub GMS URL (default: local quickstart)
        datahub_token: Optional authentication token

    Returns:
        A configured DataHubMCPAdapter ready for use with ChangeGuardAgent

    Raises:
        RuntimeError: If the MCP server is missing required tools
        ConnectionError: If the MCP server cannot be reached
    """
    try:
        client = await MCPStdioClient.connect(datahub_url, datahub_token)
    except Exception as e:
        raise ConnectionError(
            f"Failed to connect to DataHub MCP server at {datahub_url}: {e}"
        ) from e

    try:
        adapter = DataHubMCPAdapter(
            call_tool=client.call_tool,
            available_tools=client.available_tools,
            close=client.close,
        )
    except Exception:
        # The stdio client/subprocess was already started; if adapter
        # construction fails (e.g. a required tool is missing), close it
        # here so we do not leak the mcp-server-datahub subprocess.
        await client.close()
        raise
    return adapter
