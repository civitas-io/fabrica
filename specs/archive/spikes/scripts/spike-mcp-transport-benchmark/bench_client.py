"""Real MCP client-side benchmark, comparing stdio/sse/streamable_http
transports for latency, throughput under concurrency, and memory
footprint. No mocks -- every measurement is against a real running
server (subprocess for stdio, real uvicorn for sse/streamable_http).

Usage:
    python3 bench_client.py stdio
    python3 bench_client.py sse
    python3 bench_client.py streamable_http
"""
from __future__ import annotations

import gc
import json
import statistics
import sys
import time
from contextlib import AsyncExitStack

import anyio
import psutil
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

_WARMUP_CALLS = 20
_LATENCY_CALLS = 200
_THROUGHPUT_DURATION_S = 5.0
_CONCURRENCY_LEVELS = [1, 5, 10, 25, 50]

_HTTP_HOST = "127.0.0.1"
_SSE_PORT = 8971
_STREAMABLE_PORT = 8972


async def _connect(transport: str) -> tuple[AsyncExitStack, ClientSession]:
    stack = AsyncExitStack()
    if transport == "stdio":
        params = StdioServerParameters(
            command=sys.executable, args=["bench_server_stdio.py"]
        )
        read, write = await stack.enter_async_context(stdio_client(params))
    elif transport == "sse":
        read, write = await stack.enter_async_context(
            sse_client(f"http://{_HTTP_HOST}:{_SSE_PORT}/sse")
        )
    elif transport == "streamable_http":
        read, write = await stack.enter_async_context(
            streamable_http_client(f"http://{_HTTP_HOST}:{_STREAMABLE_PORT}/mcp")
        )
    else:
        raise ValueError(transport)
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return stack, session


async def _one_call(session: ClientSession, n: int) -> None:
    result = await session.call_tool("echo", {"n": n})
    assert result.content, "empty result"


async def bench_latency(transport: str) -> dict[str, float]:
    stack, session = await _connect(transport)
    try:
        for i in range(_WARMUP_CALLS):
            await _one_call(session, i)
        samples: list[float] = []
        for i in range(_LATENCY_CALLS):
            t0 = time.perf_counter()
            await _one_call(session, i)
            samples.append((time.perf_counter() - t0) * 1000.0)
    finally:
        await stack.aclose()
    samples.sort()
    return {
        "p50_ms": statistics.median(samples),
        "p95_ms": samples[int(len(samples) * 0.95)],
        "p99_ms": samples[int(len(samples) * 0.99)],
        "mean_ms": statistics.mean(samples),
        "min_ms": samples[0],
        "max_ms": samples[-1],
    }


async def _worker(session: ClientSession, deadline: float, counter: list[int]) -> None:
    i = 0
    while time.perf_counter() < deadline:
        await _one_call(session, i)
        counter[0] += 1
        i += 1


async def bench_throughput(transport: str, concurrency: int) -> dict[str, float]:
    stack, session = await _connect(transport)
    try:
        for i in range(_WARMUP_CALLS):
            await _one_call(session, i)
        counter = [0]
        deadline = time.perf_counter() + _THROUGHPUT_DURATION_S
        t0 = time.perf_counter()
        async with anyio.create_task_group() as tg:
            for _ in range(concurrency):
                tg.start_soon(_worker, session, deadline, counter)
        elapsed = time.perf_counter() - t0
    finally:
        await stack.aclose()
    return {
        "concurrency": concurrency,
        "total_calls": counter[0],
        "elapsed_s": elapsed,
        "calls_per_sec": counter[0] / elapsed,
    }


async def bench_memory(transport: str) -> dict[str, float]:
    proc = psutil.Process()
    gc.collect()
    rss_before_mb = proc.memory_info().rss / (1024 * 1024)
    stack, session = await _connect(transport)
    try:
        for i in range(_WARMUP_CALLS):
            await _one_call(session, i)
        gc.collect()
        rss_after_connect_mb = proc.memory_info().rss / (1024 * 1024)
        for i in range(2000):
            await _one_call(session, i)
        gc.collect()
        rss_after_2000_calls_mb = proc.memory_info().rss / (1024 * 1024)
    finally:
        await stack.aclose()
    return {
        "rss_before_mb": rss_before_mb,
        "rss_after_connect_mb": rss_after_connect_mb,
        "rss_after_2000_calls_mb": rss_after_2000_calls_mb,
        "connect_overhead_mb": rss_after_connect_mb - rss_before_mb,
        "growth_after_2000_calls_mb": rss_after_2000_calls_mb - rss_after_connect_mb,
    }


async def main(transport: str) -> None:
    results: dict[str, object] = {"transport": transport}

    results["latency"] = await bench_latency(transport)

    throughput_results = []
    for c in _CONCURRENCY_LEVELS:
        throughput_results.append(await bench_throughput(transport, c))
    results["throughput"] = throughput_results

    results["memory"] = await bench_memory(transport)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    anyio.run(main, sys.argv[1])
