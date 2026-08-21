"""TailscaleTunnelProvider -- priority 1 of TunnelProvider's three backends
(docs/contracts/managed-sandbox.md). Needs Tailscale Funnel specifically
(`tailscale funnel <port>`), not plain tailnet membership -- plain
Tailscale only makes a service reachable to other devices on the same
tailnet, which a managed provider's own cloud sandbox is never a member
of.

Validated end to end on real hardware, not assumed from the CLI's own
--help text: a real quick HTTP server on 127.0.0.1, funneled, reached via
a real `curl` against the public https://<node>.<tailnet>.ts.net URL --
the server's own access log showed the real inbound request.
"""

from __future__ import annotations

import asyncio
import re
import shutil

from fabrica.tunnel.errors import TunnelStartError

_URL_PATTERN = re.compile(r"https://\S+")
_START_TIMEOUT = 15.0
"""Generous relative to real, observed Funnel establishment time (returns
within a couple seconds once `tailscale` itself is running) -- real
hardware/network variance shouldn't make a correct start look like a
hang."""


class TailscaleTunnelProvider:
    """Implements the TunnelProvider Protocol via the real `tailscale`
    CLI, run as a subprocess -- no Python Tailscale client library
    dependency, matching this project's own "wrap, don't reimplement"
    discipline for every other CLI-fronted backend (`jailer`, `curl`
    against Firecracker's API).
    """

    def __init__(self, *, tailscale_binary: str = "tailscale") -> None:
        self._tailscale_binary = tailscale_binary
        self._started = False

    async def is_available(self) -> bool:
        """Real check, not just binary presence: `tailscale status` must
        also succeed, confirming the invoking machine is actually logged
        into a tailnet -- a bare `tailscale` binary with no active login
        cannot Funnel anything.
        """
        if shutil.which(self._tailscale_binary) is None:
            return False
        proc = await asyncio.create_subprocess_exec(
            self._tailscale_binary,
            "status",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0

    async def start(self, *, local_port: int) -> str:
        """Runs `tailscale funnel --bg --yes <port>` -- `--bg` returns
        immediately once Funnel is configured (tailscaled itself keeps
        the tunnel alive, no long-lived foreground process for this
        class to track, unlike CloudflareTunnelProvider); `--yes` skips
        any interactive confirmation prompt, since this runs
        unattended.
        """
        proc = await asyncio.create_subprocess_exec(
            self._tailscale_binary,
            "funnel",
            "--bg",
            "--yes",
            str(local_port),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_START_TIMEOUT)
        except TimeoutError as exc:
            proc.kill()
            raise TunnelStartError(
                f"tailscale funnel did not complete within {_START_TIMEOUT}s"
            ) from exc

        output = stdout.decode(errors="replace")
        if proc.returncode != 0:
            raise TunnelStartError(f"tailscale funnel exited {proc.returncode}: {output}")

        match = _URL_PATTERN.search(output)
        if match is None:
            raise TunnelStartError(
                f"tailscale funnel produced no parseable URL in its output: {output}"
            )
        self._started = True
        return match.group(0)

    async def stop(self) -> None:
        """`tailscale funnel reset` -- fully clears Funnel config, safe
        for this class's single-tunnel-per-instance usage pattern (one
        CallbackBridge, one tunnel). Safe to call even if start() was
        never called or failed -- `reset` is itself idempotent (a no-op
        against an already-clear config, confirmed on real hardware).
        """
        if not self._started:
            return
        proc = await asyncio.create_subprocess_exec(
            self._tailscale_binary,
            "funnel",
            "reset",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        self._started = False
