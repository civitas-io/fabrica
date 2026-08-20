"""SrtSandbox -- the Tier 1 Sandbox backend, wrapping Anthropic's Sandbox
Runtime (`srt`, @anthropic-ai/sandbox-runtime). isolation.md names `srt` as
the intended macOS Tier 1 backend; per its own README and a live check on
this machine, `srt` also supports Linux (bubblewrap+netns) and Windows
(WFP) -- one implementation covering all three platforms Kordon ships on,
not a macOS-only backend needing a separate gVisor implementation for
Linux.

Real, OS-level, default-deny network enforcement (Seatbelt / bubblewrap /
WFP, per platform) for the WHOLE process tree -- not a convention the
sandboxed code could bypass, closing the exact gap SubprocessSandbox
(Tier 0) has: sandboxed code there can `import socket` directly, since
the guest shim execs with unrestricted Python builtins. Verified live,
not assumed: an allowlisted domain resolves and connects; a
non-allowlisted domain gets a real proxy-level 403
(`CONNECT tunnel failed, response 403`).

Known, honestly-stated residual: this has been verified on macOS. `srt`
documents Linux and Windows support but neither has been run here --
don't claim cross-platform parity until each is actually exercised on
real hardware, matching this project's own "verify, don't assume"
discipline used throughout the Firecracker/srt spikes.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fabrica.sandbox._shim_runner import run_shimmed_subprocess
from fabrica.sandbox.network_policy import NetworkPolicy
from fabrica.sandbox.types import RunResult, SandboxHandle, ToolCallCallback

SRT_BINARY_NAME = "srt"

# srt's own CLI (commander.js) does not stop parsing its OWN options at
# the first positional argument -- confirmed live: `srt --settings <p>
# curl -w "..."` fails with "Could not load settings from S" because
# curl's own `-w`/`-o` flags get consumed as if they were srt's. The `--`
# separator is not optional decoration here, it is the fix -- omitting it
# is a real, reproduced bug, not a style choice.
_ARGV_SEPARATOR = "--"


def srt_available() -> bool:
    """Real binary-on-PATH check, matching firecracker_backend's own
    `_firecracker_available()` shape in dispatch.py -- a capability
    probe, never assumed from the OS name alone."""
    return shutil.which(SRT_BINARY_NAME) is not None


class SrtSandbox:
    """Implements the Sandbox protocol. Like SubprocessSandbox, no
    persistent state between boot_clean() and execute() -- a fresh srt
    settings file is written per execute() call (network policy can
    legitimately differ per action within one session, e.g. after a
    re-scope), not cached across calls.
    """

    @property
    def tier(self) -> int:
        return 1

    def __init__(
        self,
        network_policy: NetworkPolicy,
        *,
        deny_read_paths: list[str] | None = None,
        write_allow_path: str | None = None,
        allow_all_unix_sockets_on_linux: bool = False,
    ) -> None:
        """`deny_read_paths` defaults to the most commonly cited
        credential-leak vectors (`~/.ssh`, `~/.aws`) -- srt's own default
        filesystem posture is deny-then-*allow* for reads (broad read
        access unless explicitly denied), so these must be listed, not
        assumed blocked. `write_allow_path` defaults to this instance's
        own socket directory (the guest shim needs no other write access
        to function) -- callers needing a real scratch/output directory
        should pass one explicitly rather than relying on the default.
        `allow_all_unix_sockets_on_linux` trades a real, small isolation
        weakening (any Unix socket, not just the guest shim's) for
        working IPC on Linux, where srt's `allowUnixSockets` is
        explicitly path-filtering-incapable per its own schema -- off by
        default; callers running on Linux must opt in, matching the "no
        silent downgrade" discipline this whole project holds elsewhere.
        """
        self._network_policy = network_policy
        self._deny_read_paths = deny_read_paths or ["~/.ssh", "~/.aws"]
        self._allow_all_unix_sockets_on_linux = allow_all_unix_sockets_on_linux
        self._socket_dir = Path("/tmp") / f"fabrica-srt-{uuid.uuid4().hex[:8]}"
        self._socket_dir.mkdir(parents=True, exist_ok=True)
        self._write_allow_path = write_allow_path or str(self._socket_dir)

    async def boot_clean(self) -> SandboxHandle:
        return SandboxHandle(id=uuid.uuid4().hex[:8], tier=1)

    async def execute(
        self,
        handle: SandboxHandle,
        code: str,
        *,
        on_tool_call: ToolCallCallback,
        timeout: float,
        tool_call_timeout: float | None = None,
    ) -> RunResult:
        ipc_path = self._socket_dir / f"{handle.id}.sock"
        settings_path = self._socket_dir / f"{handle.id}-settings.json"
        settings_path.write_text(json.dumps(self._build_settings(ipc_path)))
        try:
            return await run_shimmed_subprocess(
                command_prefix=[
                    SRT_BINARY_NAME,
                    "--settings",
                    str(settings_path),
                    _ARGV_SEPARATOR,
                ],
                ipc_path=ipc_path,
                sandbox_id=handle.id,
                code=code,
                on_tool_call=on_tool_call,
                timeout=timeout,
                tool_call_timeout=tool_call_timeout,
            )
        finally:
            settings_path.unlink(missing_ok=True)

    def _build_settings(self, ipc_path: Path) -> dict[str, object]:
        return {
            "filesystem": {
                "denyRead": list(self._deny_read_paths),
                "allowWrite": [self._write_allow_path],
                "denyWrite": [],
            },
            "network": self._network_policy.to_srt_network_config(
                # macOS: allow exactly this call's own ipc socket, nothing
                # else. Linux: path-based allowlisting is unsupported by
                # srt's own schema, so this falls back to the broader
                # opt-in flag if the caller set one; if not, Unix sockets
                # stay fully blocked and the guest shim's own callback
                # bridge will fail to connect -- a loud, real failure,
                # not a silent one.
                allow_unix_socket_path=str(ipc_path),
                allow_all_unix_sockets=self._allow_all_unix_sockets_on_linux,
            ),
        }

    async def terminate(self, handle: SandboxHandle) -> None:
        (self._socket_dir / f"{handle.id}.sock").unlink(missing_ok=True)
        (self._socket_dir / f"{handle.id}-settings.json").unlink(missing_ok=True)

    async def health_check(self) -> bool:
        return srt_available() and (Path(__file__).parent / "_guest_shim.py").exists()
