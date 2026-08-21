"""Shared test infra for tests/mcp/ -- a real "wait until actually
listening" helper, not a fixed sleep.

Real bug found via a genuine CI failure, not theorized: both
test_server.py's and test_server_stress.py's own `_RunningHttpServer`
fixtures used `await asyncio.sleep(0.4)` after starting uvicorn in a
background task, assuming that was always enough time for the real
socket bind to complete before a test's first client connection. This
passed reliably on a fast, uncontested local dev machine but failed on
GitHub Actions' own (more resource-constrained, shared) runner with a
real `MCPError(-32000, 'SSE stream ended without a response')` --
consistent with a client connecting before uvicorn had actually started
accepting connections, not a logic bug in the server itself.
"""

from __future__ import annotations

import asyncio
import contextlib


async def wait_for_port_open(host: str, port: int, *, timeout: float = 5.0) -> None:
    """Polls a real TCP connect attempt until it succeeds, instead of
    assuming a fixed sleep is always long enough. Raises TimeoutError
    (via asyncio.timeout) if the port never opens within `timeout` --
    a real, actionable failure instead of proceeding to a test that
    would otherwise fail with a confusing, indirect connection error.
    """
    async with asyncio.timeout(timeout):
        while True:
            try:
                _, writer = await asyncio.open_connection(host, port)
            except (ConnectionRefusedError, OSError):
                await asyncio.sleep(0.02)
                continue
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return
