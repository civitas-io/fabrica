"""Errors for TunnelProvider -- see docs/contracts/managed-sandbox.md."""

from __future__ import annotations


class TunnelError(Exception):
    """Base for all TunnelProvider errors."""


class TunnelStartError(TunnelError):
    """start() failed -- the underlying CLI tool either isn't installed,
    isn't authenticated, or exited non-zero/produced unexpected output.
    Raised instead of silently returning a bogus or empty URL -- a
    CallbackBridge that got no real externally-reachable URL must never
    proceed as if it had one.
    """


class TunnelNotAvailableError(TunnelError):
    """The backend's underlying CLI tool isn't installed / isn't
    authenticated -- checked proactively by `is_available()`, not just
    discovered as a start() failure. Lets a caller pick the next backend
    in TunnelProvider's own decided priority order (Tailscale, then
    Cloudflare, then ngrok) without needing to attempt and fail first.
    """
