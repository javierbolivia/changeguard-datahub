"""Focused lifecycle tests for the real MCP stdio transport."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from contract_sentinel.mcp_connection import MCPStdioClient


class _EOFReader:
    async def readline(self):
        return b""


class _FailingReader:
    async def readline(self):
        raise RuntimeError("stdout pipe broke")


class _FakeStdin:
    def __init__(self):
        self.client = None
        self.registered_before_write = False
        self.closed = False

    def write(self, data):
        request_id = json.loads(data.decode())["id"]
        future = self.client._pending.get(request_id)
        self.registered_before_write = future is not None
        if future is not None:
            future.set_result({"ok": True})

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, stdout=None, stderr=None):
        self.stdin = _FakeStdin()
        self.stdout = stdout or _EOFReader()
        self.stderr = stderr or _EOFReader()
        self.returncode = None
        self.pid = 1234

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode or 0


class MCPStdioClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_future_is_registered_before_stdin_write(self):
        process = _FakeProcess()
        client = MCPStdioClient(process)
        process.stdin.client = client

        result = await asyncio.wait_for(
            client._send_request("test/method", {}), timeout=0.1
        )

        self.assertEqual(result, {"ok": True})
        self.assertTrue(process.stdin.registered_before_write)
        self.assertEqual(client._pending, {})

    async def test_reader_failure_immediately_fails_pending_requests(self):
        process = _FakeProcess(stdout=_FailingReader())
        client = MCPStdioClient(process)
        future = asyncio.get_running_loop().create_future()
        client._pending[7] = future

        await client._read_responses()

        with self.assertRaisesRegex(ConnectionError, "stdout pipe broke"):
            await future
        self.assertEqual(client._pending, {})

    async def test_stderr_is_drained_and_only_a_bounded_tail_is_retained(self):
        stderr = asyncio.StreamReader()
        for index in range(60):
            stderr.feed_data(f"line-{index}\n".encode())
        stderr.feed_eof()
        client = MCPStdioClient(_FakeProcess(stderr=stderr))

        await client._drain_stderr()

        self.assertEqual(len(client._stderr_tail), 50)
        self.assertEqual(client._stderr_tail[0], "line-10")
        self.assertEqual(client._stderr_tail[-1], "line-59")

    async def test_initialization_failure_closes_started_process(self):
        process = _FakeProcess()
        initialize = AsyncMock(side_effect=TimeoutError("initialize timed out"))
        close = AsyncMock()

        with (
            patch(
                "contract_sentinel.mcp_connection.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ),
            patch.object(MCPStdioClient, "_initialize", new=initialize),
            patch.object(MCPStdioClient, "close", new=close),
        ):
            with self.assertRaisesRegex(TimeoutError, "initialize timed out"):
                await MCPStdioClient.connect()

        close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
