"""Tests for select_tunnel_provider() -- real environment-based selection,
not mocked. The "none available" case is tested by genuinely making
`shutil.which` unable to find either binary (an empty PATH), not by
mocking this module's own code -- matching this project's general
preference for real behavior over mocked internals.
"""

from __future__ import annotations

import pytest

from fabrica.tunnel.cloudflare_provider import CloudflareTunnelProvider
from fabrica.tunnel.dispatch import select_tunnel_provider
from fabrica.tunnel.errors import TunnelNotAvailableError
from fabrica.tunnel.tailscale_provider import TailscaleTunnelProvider


async def test_select_tunnel_provider_raises_when_none_available(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A genuinely empty PATH -- shutil.which("tailscale")/("cloudflared")
    # both really return None, not a mocked function call.
    monkeypatch.setenv("PATH", "")
    with pytest.raises(TunnelNotAvailableError):
        await select_tunnel_provider()


async def test_select_tunnel_provider_returns_something_real_when_available() -> None:
    # Real check against whatever's actually installed on this machine --
    # skips outright if neither tool happens to be present here, since
    # that's this test's own real precondition, not a hardware-gate
    # convention to work around.
    provider = None
    try:
        provider = await select_tunnel_provider()
    except TunnelNotAvailableError:
        pytest.skip("neither tailscale nor cloudflared is available on this machine")
    assert isinstance(provider, (TailscaleTunnelProvider, CloudflareTunnelProvider))
