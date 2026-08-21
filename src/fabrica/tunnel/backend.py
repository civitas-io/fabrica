"""The TunnelProvider protocol -- see docs/contracts/managed-sandbox.md's
own "TunnelProvider -- resolved, a clean interface, three backends, in
priority order" section.

Implementations: TailscaleTunnelProvider (priority 1, implemented -- needs
Tailscale Funnel specifically, not plain tailnet membership; validated end
to end on real hardware, a real public URL reaching a real local server),
CloudflareTunnelProvider (priority 2, implemented -- cloudflared's
account-less "quick tunnel" mode, also validated end to end on real
hardware). NgrokTunnelProvider (priority 3) remains not implemented -- see
this package's own README note for why, not silently omitted.

CallbackBridge composes one of these when its host/port aren't already
publicly reachable; when they are (a real deployment with a public or
VPC-peered address), no TunnelProvider is needed at all -- this Protocol
exists purely for local dev / private / NAT'd deployments.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TunnelProvider(Protocol):
    async def is_available(self) -> bool:
        """A real, proactive check -- confirms the underlying CLI tool is
        installed AND (where the backend needs it) authenticated, without
        needing to attempt and fail a real start() first. Lets
        `select_tunnel_provider()` walk the decided priority order
        (Tailscale, then Cloudflare, then ngrok) and pick the first one
        that will actually work, rather than the first one merely
        installed.
        """
        ...

    async def start(self, *, local_port: int) -> str:
        """Establishes the tunnel. Returns the externally-reachable URL
        that resolves to local_port -- this becomes CallbackBridge's
        externally-advertised callback_url, not local_port's own bare
        address.

        Raises:
            TunnelStartError: the underlying CLI tool exited non-zero,
                or never produced a real, parseable URL within a
                reasonable timeout.
        """
        ...

    async def stop(self) -> None:
        """Tears down the tunnel. Called whenever CallbackBridge itself
        shuts down -- not per-execution; a tunnel is a longer-lived
        resource than one run's callback route. Safe to call even if
        start() was never called or already failed -- matches
        Sandbox.close()'s own idempotent-teardown discipline.
        """
        ...
