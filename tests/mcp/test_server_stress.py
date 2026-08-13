"""FabricaMCPServer under real concurrent multi-tenant load -- closes
contracts/mcp-server.md's open item 3: the single-token-per-connection
shape was already confirmed working (tests/mcp/test_server.py), but
"doesn't stress-test many simultaneous distinct agent_ids against shared
SandboxPool/Retriever state" under real concurrent load. This file does.

A REAL uvicorn server, REAL concurrent mcp.client.streamable_http
connections (one per simulated agent, via asyncio.gather -- not
sequential), REAL bearer tokens each resolving to a distinct agent_id,
REAL SubprocessSandbox executions contending for the same SandboxPool.
Nothing mocked.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import httpx2
import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from fabrica.civitas_bridge import CivitasBridge
from fabrica.mcp.server import FabricaMCPServer, ServerTransportConfig

_HTTP_HOST = "127.0.0.1"
_HTTP_PORT = 8932  # distinct from test_server.py's 8931 -- avoids any port reuse race
_HTTP_URL = f"http://{_HTTP_HOST}:{_HTTP_PORT}/mcp"
_N_AGENTS = 10


class _MultiTokenAuthenticator:
    """Real TokenAuthenticator -- N distinct tokens, each resolving to its
    own distinct agent_id. The actual multi-tenant shape this stress test
    exists to exercise, not a single shared identity.
    """

    def __init__(self, n: int) -> None:
        self._tokens = {f"token-{i}": f"stress-agent-{i}" for i in range(n)}

    async def authenticate(self, token: str) -> str | None:
        return self._tokens.get(token)


class _RunningHttpServer:
    def __init__(self, *, warm_size: int, max_concurrent: int) -> None:
        self._warm_size = warm_size
        self._max_concurrent = max_concurrent
        self._server: FabricaMCPServer | None = None
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> FabricaMCPServer:
        fabrica = await CivitasBridge(
            allow_ungoverned=True, warm_size=self._warm_size, max_concurrent=self._max_concurrent
        ).build()
        transport = ServerTransportConfig(
            kind="http",
            host=_HTTP_HOST,
            port=_HTTP_PORT,
            authenticator=_MultiTokenAuthenticator(_N_AGENTS),
        )
        self._server = FabricaMCPServer(
            fabrica, transport, allow_weak_isolation_for_external_callers=True
        )
        self._task = asyncio.ensure_future(self._server.start())
        await asyncio.sleep(0.4)
        return self._server

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None and self._task is not None
        await self._server.stop()
        await self._task


async def _call_tool_as_agent(
    agent_index: int, tool: str, arguments: dict[str, Any], *, timeout: float = 10.0
) -> types.CallToolResult:
    """Opens its OWN real connection (own bearer token, own ClientSession)
    -- simulating N genuinely separate tenants, not N calls multiplexed
    over one shared session.

    `timeout` defaults generously above httpx2's own 5s default -- a real
    flake was caught here (not hidden): under full-suite CPU load, the
    deliberately extreme max_concurrent=1/10-agent serialization scenario
    (below) can legitimately take longer than httpx2's short default to
    reach the back of its queue, causing the CLIENT to give up mid-request
    (`MCPError(-32000, 'SSE stream ended without a response')`) even
    though the server itself was still working correctly. A test-timeout
    problem, not a production bug -- fixed by giving the client a longer
    timeout matching this scenario's own realistic worst-case duration,
    not by changing FabricaMCPServer/SandboxPool.
    """
    client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer token-{agent_index}"}, timeout=httpx2.Timeout(timeout)
    )
    async with contextlib.AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(
            streamable_http_client(_HTTP_URL, http_client=client)
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return await session.call_tool(tool, arguments)


def _payload(result: types.CallToolResult) -> Any:
    first = result.content[0]
    assert isinstance(first, types.TextContent)
    return json.loads(first.text)


class TestConcurrentMemoryIsolation:
    async def test_n_agents_concurrently_write_and_search_without_cross_contamination(
        self,
    ) -> None:
        # Default pool sizing (warm_size=2, max_concurrent=4) -- memory
        # writes/searches don't touch SandboxPool at all, so this isolates
        # the Retriever/MemoryStore concurrency question specifically.
        async with _RunningHttpServer(warm_size=2, max_concurrent=4):
            write_results = await asyncio.gather(
                *[
                    _call_tool_as_agent(
                        i,
                        "fabrica_memory_write",
                        {"content": f"secret fact belonging to agent {i}"},
                    )
                    for i in range(_N_AGENTS)
                ]
            )
            for result in write_results:
                assert result.is_error is not True

            search_results = await asyncio.gather(
                *[
                    _call_tool_as_agent(
                        i, "fabrica_memory_search", {"query": "secret fact", "limit": 20}
                    )
                    for i in range(_N_AGENTS)
                ]
            )

        for i, result in enumerate(search_results):
            items = _payload(result)
            # Each agent's search sees EXACTLY its own item -- proving
            # Scope isolation holds under real concurrent write/search
            # pressure across the shared Retriever/MemoryStore, not just
            # sequentially (already covered by tests/mcp/test_server.py).
            assert len(items) == 1, f"agent {i} saw {len(items)} items, expected exactly 1"
            assert items[0]["content"] == f"secret fact belonging to agent {i}"


class TestConcurrentSandboxContention:
    async def test_n_agents_exceed_max_concurrent_and_all_still_succeed_correctly(
        self,
    ) -> None:
        # max_concurrent=4, but 10 agents run code-mode concurrently --
        # deliberately exceeds the pool's own SandboxPoolExhaustedError
        # bound in this test to force real bounded-overflow contention
        # (contracts/sandbox.md §6/§7), not just N <= max_concurrent
        # traffic that would never actually touch the queuing path.
        max_concurrent = 4
        assert _N_AGENTS > max_concurrent

        async with _RunningHttpServer(warm_size=2, max_concurrent=max_concurrent):
            results = await asyncio.gather(
                *[
                    _call_tool_as_agent(i, "fabrica_run_code", {"code": f"print({i} * 100)"})
                    for i in range(_N_AGENTS)
                ],
                return_exceptions=True,
            )

        # No unhandled exception escaped the gather -- real concurrent
        # contention against a shared SandboxPool must resolve to a
        # routine MCP-level outcome (success or is_error), never a raw
        # crash reaching the client.
        for i, result in enumerate(results):
            assert not isinstance(result, BaseException), f"agent {i} raised: {result!r}"

        for i, result in enumerate(results):
            assert isinstance(result, types.CallToolResult)
            assert result.is_error is not True, f"agent {i}'s run_code failed: {result.content}"
            payload = _payload(result)
            assert payload["success"] is True
            # Each agent's own distinct computation came back correctly --
            # proving no cross-talk between concurrent sandbox executions
            # sharing the same pool (a queued/overflowed request getting
            # served with a stale or another agent's result would be a
            # real isolation bug, not just a performance one).
            assert str(i * 100) in payload["stdout"]


class TestConcurrentSandboxContentionUnderHeavySerialization:
    async def test_max_concurrent_1_serializes_without_hanging_or_crashing(self) -> None:
        # The extreme case: max_concurrent=1 forces every one of the 10
        # concurrent agents through a single sandbox slot, one at a time --
        # real bounded-overflow queuing under maximum contention, proven
        # to complete within a bounded overall wait rather than deadlock.
        async with _RunningHttpServer(warm_size=1, max_concurrent=1):
            results = await asyncio.wait_for(
                asyncio.gather(
                    *[
                        # A longer per-client timeout than the other tests --
                        # the LAST agents in a 10-deep queue against ONE
                        # concurrent slot can legitimately wait several
                        # seconds under real system load; see
                        # _call_tool_as_agent's own docstring for the real
                        # flake this caught.
                        _call_tool_as_agent(
                            i, "fabrica_run_code", {"code": f"print({i} * 100)"}, timeout=25.0
                        )
                        for i in range(_N_AGENTS)
                    ],
                    return_exceptions=True,
                ),
                timeout=30.0,  # the whole gather must finish well within this -- proves no hang
            )

        for i, result in enumerate(results):
            assert not isinstance(result, BaseException), f"agent {i} raised: {result!r}"
            assert isinstance(result, types.CallToolResult)
            assert result.is_error is not True, f"agent {i}'s run_code failed: {result.content}"
            payload = _payload(result)
            assert str(i * 100) in payload["stdout"]
