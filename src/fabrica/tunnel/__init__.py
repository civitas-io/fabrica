"""TunnelProvider -- real, externally-reachable URLs for a local
CallbackBridge port. See docs/contracts/managed-sandbox.md's own
"TunnelProvider -- resolved, a clean interface, three backends, in
priority order" section for the full contract.
"""

from fabrica.tunnel.backend import TunnelProvider
from fabrica.tunnel.cloudflare_provider import CloudflareTunnelProvider
from fabrica.tunnel.dispatch import select_tunnel_provider
from fabrica.tunnel.errors import TunnelError, TunnelNotAvailableError, TunnelStartError
from fabrica.tunnel.tailscale_provider import TailscaleTunnelProvider

__all__ = [
    "CloudflareTunnelProvider",
    "TailscaleTunnelProvider",
    "TunnelError",
    "TunnelNotAvailableError",
    "TunnelProvider",
    "TunnelStartError",
    "select_tunnel_provider",
]
