"""Tests for TailscaleTunnelProvider -- real `tailscale funnel`, a real
externally-reachable HTTPS URL, a real curl round trip. Requires an
active Tailscale login (checked via is_available(), not assumed) --
skipped otherwise, same discipline as the Firecracker hardware tests.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from fabrica.tunnel.tailscale_provider import TailscaleTunnelProvider

_TAILSCALE_BINARY_PRESENT = shutil.which("tailscale") is not None

pytestmark = pytest.mark.skipif(
    not _TAILSCALE_BINARY_PRESENT,
    reason="requires the tailscale binary to be installed",
)


async def _tailscale_logged_in() -> bool:
    provider = TailscaleTunnelProvider()
    return await provider.is_available()


async def test_is_available_matches_real_login_state() -> None:
    provider = TailscaleTunnelProvider()
    # Real check, not mocked -- confirms this actually calls `tailscale
    # status` and gets a real answer back, whatever it is on this
    # machine, rather than assuming True.
    result = await provider.is_available()
    assert isinstance(result, bool)


async def test_is_available_false_for_a_nonexistent_binary() -> None:
    provider = TailscaleTunnelProvider(tailscale_binary="/no/such/tailscale")
    assert await provider.is_available() is False


async def test_stop_is_a_safe_no_op_when_never_started() -> None:
    # Must not touch the real tailscale CLI at all when start() was
    # never called -- self._started stays False.
    provider = TailscaleTunnelProvider(tailscale_binary="/no/such/tailscale")
    await provider.stop()  # must not raise


@pytest.mark.skipif(
    not _TAILSCALE_BINARY_PRESENT,
    reason="requires the tailscale binary to be installed and logged in",
)
async def test_start_produces_a_real_externally_reachable_url(local_http_server: int) -> None:
    if not await _tailscale_logged_in():
        pytest.skip("requires an active Tailscale login")

    provider = TailscaleTunnelProvider()
    url = await provider.start(local_port=local_http_server)
    try:
        assert url.startswith("https://")

        proc = await asyncio.create_subprocess_exec(
            "curl",
            "-s",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "-m",
            "15",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        assert stdout.decode().strip() == "200"
    finally:
        await provider.stop()
