"""select_tunnel_provider() -- real, automatic selection among
TunnelProvider's three backends, mirroring
fabrica.sandbox.dispatch.select_sandbox_backend()'s own shape (a decided
priority order, walked in order, first one that reports itself available
wins).

Priority order, per docs/contracts/managed-sandbox.md: Tailscale (already
part of this project's own infrastructure story, needs no separate
third-party account beyond an existing tailnet), then Cloudflare (free,
no account required for a quick tunnel), then ngrok (not yet implemented
-- see this package's own README note).
"""

from __future__ import annotations

from fabrica.tunnel.backend import TunnelProvider
from fabrica.tunnel.cloudflare_provider import CloudflareTunnelProvider
from fabrica.tunnel.errors import TunnelNotAvailableError
from fabrica.tunnel.tailscale_provider import TailscaleTunnelProvider


async def select_tunnel_provider() -> TunnelProvider:
    """Walks the decided priority order, returning the first backend
    whose `is_available()` check passes. Raises `TunnelNotAvailableError`
    if none are -- a caller that actually needs a tunnel (no public/
    VPC-peered address available) must know explicitly that none of the
    supported mechanisms are usable here, not silently get a
    non-functional provider back.
    """
    for provider in (TailscaleTunnelProvider(), CloudflareTunnelProvider()):
        if await provider.is_available():
            return provider
    raise TunnelNotAvailableError(
        "no TunnelProvider backend is available -- checked Tailscale (needs "
        "the `tailscale` binary and an active tailnet login) and Cloudflare "
        "(needs the `cloudflared` binary); ngrok is not yet implemented"
    )
