"""SrtIsolation -- wraps an MCP stdio subprocess launch in Anthropic's
`srt` (Sandbox Runtime), replacing `civitas-contrib/packages/fabrica`'s
`BubblewrapSandbox`. See docs/contracts/mcp-integration.md's Isolation
section and "Correction found during implementation" note.

`srt` unifies `bwrap` (Linux) and `sandbox-exec` (macOS) under one CLI,
closing BubblewrapSandbox's Linux-only gap -- mcp-integration.md's resolved
isolation mechanism. Not used through SandboxPool at all: persistent MCP
connections don't fit SandboxPool's ephemeral-execution lifecycle.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from fabrica.mcp.errors import IsolationUnavailableError, UnsupportedSandboxConfigurationError
from fabrica.mcp.types import SandboxConfig


class SrtIsolation:
    """Wraps an MCP subprocess command with `srt`.

    Usage::

        isolation = SrtIsolation(config)
        isolation.check_or_raise()
        cmd, args = isolation.wrap("python", ["-m", "my_mcp_server"])
    """

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config

    @staticmethod
    def available() -> bool:
        """Return True if `srt` is on PATH."""
        return shutil.which("srt") is not None

    def check_or_raise(self) -> None:
        """
        Raises:
            UnsupportedSandboxConfigurationError: network="allow" was
                requested -- srt structurally refuses this (see the
                module docstring); no available() check applies, since
                the problem isn't srt's absence, it's the request itself.
            IsolationUnavailableError: srt is not on PATH and
                config.allow_unsandboxed is False.
        """
        if self._config.network == "allow":
            raise UnsupportedSandboxConfigurationError(
                "sandbox.network='allow' cannot be honored by srt -- it structurally "
                "refuses an unrestricted-network configuration (confirmed by running "
                "srt directly: an 'allowedDomains: [\"*\"]' entry is a hard config "
                "error, not a warning). Set sandbox.enabled=False with "
                "allow_unsandboxed=True for this server instead if it genuinely "
                "needs broad outbound network access."
            )
        if not self.available() and not self._config.allow_unsandboxed:
            raise IsolationUnavailableError(
                "sandbox.enabled=True but 'srt' (@anthropic-ai/sandbox-runtime) is not "
                "available on PATH. Install it with "
                "'npm install -g @anthropic-ai/sandbox-runtime', or set "
                "allow_unsandboxed=True to run this MCP server unsandboxed."
            )

    def wrap(self, command: str, args: list[str]) -> tuple[str, list[str]]:
        """Return ("srt", srt_args) that runs `command args` inside the
        sandbox. If srt is unavailable (only reachable when
        allow_unsandboxed=True -- check_or_raise already enforced this),
        returns (command, args) unchanged -- an explicit, already-opted-into
        unsandboxed fallback, not a silent one.
        """
        if not self.available():
            return command, args

        settings_path = self._write_settings_file()
        return "srt", ["-s", str(settings_path), "--", command, *args]

    def _write_settings_file(self) -> Path:
        """srt takes a JSON settings file (-s <path>), not inline flags.
        Written fresh per connect() call -- MCP servers are long-lived,
        persistent connections (one per agent per server), so the
        per-connect file-write cost is negligible, unlike SandboxPool's
        per-execution hot path.

        network="deny" -> allowedDomains: [] (tested directly: this
        genuinely blocks all outbound network access). network="allow" is
        unreachable here -- check_or_raise already rejected it.

        Filesystem: only "rw" mounts translate to an explicit allowWrite
        entry. "ro" mounts are NOT added to any explicit allow-list --
        srt's own default posture already allows reads everywhere unless
        denied (contracts/mcp-integration.md's SandboxConfig only ever
        adds mounts, never denies a previously-open path), so a "ro"
        mount needs no entry to already be readable; it just must not
        appear in allowWrite, which it doesn't.
        """
        settings = {
            "network": {"allowedDomains": [], "deniedDomains": []},
            "filesystem": {
                "denyRead": [],
                "allowWrite": [m.path for m in self._config.filesystem if m.mode == "rw"],
                "denyWrite": [],
            },
        }
        fd, path_str = tempfile.mkstemp(prefix="fabrica-srt-", suffix=".json")
        path = Path(path_str)
        with open(fd, "w") as f:
            json.dump(settings, f)
        return path
