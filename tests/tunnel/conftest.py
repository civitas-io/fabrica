"""Shared fixtures for TunnelProvider tests -- a real local HTTP server
each "actually reachable" test tunnels to and curls, not a mock."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator

import pytest


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture
async def local_http_server() -> AsyncIterator[int]:
    """A real `python3 -m http.server` subprocess bound to 127.0.0.1 on a
    free port -- the thing every real TunnelProvider test tunnels to."""
    port = _free_local_port()
    process = await asyncio.create_subprocess_exec(
        "python3",
        "-m",
        "http.server",
        str(port),
        "--bind",
        "127.0.0.1",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    # Give it a moment to actually start listening before any test tries
    # to tunnel to it.
    await asyncio.sleep(0.5)
    try:
        yield port
    finally:
        process.kill()
        await process.wait()
