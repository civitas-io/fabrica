"""FirecrackerSandbox -- the Tier 2 self-hosted Sandbox backend
(isolation.md: microVM, own kernel, hardware-grade isolation).

Real, validated mechanism, not a guess: SPIKE-firecracker-boot-restore-latency.md
proved Firecracker's boot/snapshot/restore timing on real bare-metal hardware;
SPIKE-firecracker-vsock-callback-bridge.md proved a real, bidirectional vsock
round trip between a Firecracker guest running the ACTUAL
_firecracker_guest_shim.py and a real host-side listener, on the same
hardware -- confirmed with a real tool call crossing the VM boundary and a
real result returning.

v1 SCOPE, decided deliberately, not by oversight: `boot_clean()` here always
COLD BOOTS -- it does not restore from a snapshot. `contracts/sandbox.md`'s
own docstring already allows this ("Boot, OR restore-from-snapshot..."), so
this isn't a contract violation, but it IS a real, named limitation worth
stating plainly: cold boot to real userspace readiness measured ~1,055ms in
the boot/restore spike, far slower than SubprocessSandbox's near-zero cost.
Snapshot/restore combined WITH the vsock callback bridge is a genuinely
separate, unvalidated combination -- neither spike tested restoring a
snapshot of a guest that already has a live vsock connection established.
Shipping a correct, cold-boot-only v1 now and validating snapshot+vsock
together as a real, focused follow-up (matching this project's "ship the
default, revisit if forced" discipline) was chosen over risking a third
unvalidated mechanism before anything real exists at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import struct
import time
import uuid
from pathlib import Path
from typing import Any

from fabrica.sandbox.errors import SandboxCrashedError, SandboxTimeoutError
from fabrica.sandbox.types import MAX_STDOUT_BYTES, RunResult, SandboxHandle, ToolCallCallback

_HOST_VSOCK_PORT = 5555  # matches _firecracker_guest_shim.py's own constant
_BOOT_READY_TIMEOUT = 10.0
"""Generous relative to the spike's measured ~1,055ms real-userspace-ready
figure -- real hardware/load variance shouldn't make a correct boot look
like a hang."""


class _InstanceState:
    """Everything FirecrackerSandbox needs to track per live handle --
    kept out of SandboxHandle itself, which contracts/sandbox.md requires
    stay an opaque, backend-agnostic reference callers must not depend on.
    """

    def __init__(
        self,
        *,
        api_sock: Path,
        vsock_uds: Path,
        rootfs_copy: Path,
        console_log: Path,
        process: asyncio.subprocess.Process,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        vsock_server: asyncio.AbstractServer,
    ) -> None:
        self.api_sock = api_sock
        self.vsock_uds = vsock_uds
        self.rootfs_copy = rootfs_copy
        self.console_log = console_log
        self.process = process
        self.reader = reader
        self.writer = writer
        self.vsock_server = vsock_server


async def _send(writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
    body = json.dumps(message).encode()
    writer.write(struct.pack(">I", len(body)) + body)
    await writer.drain()


async def _recv(reader: asyncio.StreamReader) -> dict[str, Any]:
    length_bytes = await reader.readexactly(4)
    (length,) = struct.unpack(">I", length_bytes)
    body = await reader.readexactly(length)
    result: dict[str, Any] = json.loads(body)
    return result


def _cleanup_files(
    *, api_sock: Path, rootfs_copy: Path, console_log: Path, vsock_uds: Path
) -> None:
    """Shared by every path that tears an instance down -- successful
    terminate() AND every boot_clean() failure branch. A real leak was
    found (and only partially fixed at first) by inspecting /tmp after
    real test runs, not assumed complete: boot_clean()'s own error paths
    left every one of these files behind, not just terminate()'s.
    """
    for path in (
        api_sock,
        rootfs_copy,
        console_log,
        vsock_uds,
        Path(f"{vsock_uds}_{_HOST_VSOCK_PORT}"),
    ):
        path.unlink(missing_ok=True)


async def _api_put(api_sock: Path, path: str, body: dict[str, Any]) -> None:
    """Firecracker's real REST API, over its real Unix-socket control
    plane (validated in both spikes) -- shells out to `curl`, matching the
    exact, validated command shape from both spike scripts, rather than
    reimplementing raw HTTP-over-UDS or adding a new HTTP-client
    dependency for one small, already-proven integration point.
    """
    proc = await asyncio.create_subprocess_exec(
        "curl",
        "-s",
        "-X",
        "PUT",
        "--unix-socket",
        str(api_sock),
        "-d",
        json.dumps(body),
        f"http://localhost{path}",
        "-o",
        "/dev/null",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


class FirecrackerSandbox:
    """Implements the Sandbox protocol. Every boot_clean() starts a real,
    fresh `firecracker` process; every terminate() kills it -- no reuse,
    matching SandboxPool's always-terminate-never-reuse rule exactly.
    """

    @property
    def tier(self) -> int:
        return 2

    def __init__(
        self,
        *,
        firecracker_binary: str,
        kernel_image_path: str,
        base_rootfs_path: str,
        vcpu_count: int = 2,
        mem_size_mib: int = 512,
        guest_shim_path: str = "/tmp/guest_shim.py",
        socket_dir: str = "/tmp",
    ) -> None:
        """`kernel_image_path`/`base_rootfs_path` are real, deployment-
        specific artifacts (a vmlinux image, an ext4 rootfs with
        `_firecracker_guest_shim.py` already baked in at
        `guest_shim_path`) -- fully-constructed configuration this class
        receives, never builds itself. Baking the shim into the rootfs
        image is real, separate work (see
        SPIKE-firecracker-vsock-callback-bridge.md's own recommendation
        3) -- this class assumes it has already happened, the same way
        SubprocessSandbox assumes Python itself is already installed on
        its host.
        """
        self._firecracker_binary = firecracker_binary
        self._kernel_image_path = kernel_image_path
        self._base_rootfs_path = base_rootfs_path
        self._vcpu_count = vcpu_count
        self._mem_size_mib = mem_size_mib
        self._guest_shim_path = guest_shim_path
        self._socket_dir = Path(socket_dir)
        self._instances: dict[str, _InstanceState] = {}

    async def boot_clean(self) -> SandboxHandle:
        """Cold-boots a fresh guest (see module docstring for why v1
        doesn't restore from a snapshot), waits for the real guest-shim's
        "ready" message -- proof of actual userspace readiness, not just
        Firecracker's own VMM=Running signal (the exact distinction
        SPIKE-firecracker-boot-restore-latency.md found matters).

        Raises:
            SandboxCrashedError: the guest never sent "ready" within
                _BOOT_READY_TIMEOUT.
        """
        instance_id = uuid.uuid4().hex[:8]
        api_sock = self._socket_dir / f"fc-{instance_id}-api.sock"
        vsock_uds = self._socket_dir / f"fc-{instance_id}-vsock.sock"
        rootfs_copy = self._socket_dir / f"fc-{instance_id}-rootfs.ext4"
        console_log = self._socket_dir / f"fc-{instance_id}-console.log"

        # A fresh rootfs copy per instance -- not just memory-level
        # isolation, matching SandboxPool's always-terminate-never-reuse
        # rule at the disk level too (a used rootfs may carry ext4
        # journal state needing recovery, confirmed a real, sharp-edged
        # issue in the vsock spike -- a fresh copy sidesteps it entirely,
        # not just isolates arbitrary code's writes from the next run).
        shutil.copyfile(self._base_rootfs_path, rootfs_copy)

        vsock_connection: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
            asyncio.get_event_loop().create_future()
        )

        async def _accept_guest_connection(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            if not vsock_connection.done():
                vsock_connection.set_result((reader, writer))

        vsock_server = await asyncio.start_unix_server(
            _accept_guest_connection, path=f"{vsock_uds}_{_HOST_VSOCK_PORT}"
        )

        process = await asyncio.create_subprocess_exec(
            self._firecracker_binary,
            "--api-sock",
            str(api_sock),
            stdout=open(console_log, "wb"),  # noqa: SIM115 -- lifetime matches the subprocess, not this scope
            stderr=asyncio.subprocess.STDOUT,
        )

        # Firecracker's API socket takes a moment to appear after spawn --
        # the same real timing this project's spikes already accounted for.
        for _ in range(100):
            if api_sock.exists():
                break
            await asyncio.sleep(0.01)

        boot_args = (
            f"console=ttyS0 reboot=k panic=1 init=/usr/bin/python3 -- {self._guest_shim_path}"
        )
        await _api_put(
            api_sock,
            "/boot-source",
            {"kernel_image_path": self._kernel_image_path, "boot_args": boot_args},
        )
        await _api_put(
            api_sock,
            "/drives/rootfs",
            {
                "drive_id": "rootfs",
                "path_on_host": str(rootfs_copy),
                "is_root_device": True,
                "is_read_only": False,
            },
        )
        await _api_put(
            api_sock,
            "/machine-config",
            {"vcpu_count": self._vcpu_count, "mem_size_mib": self._mem_size_mib},
        )
        await _api_put(api_sock, "/vsock", {"guest_cid": 3, "uds_path": str(vsock_uds)})
        await _api_put(api_sock, "/actions", {"action_type": "InstanceStart"})

        try:
            reader, writer = await asyncio.wait_for(vsock_connection, timeout=_BOOT_READY_TIMEOUT)
            ready_message = await asyncio.wait_for(_recv(reader), timeout=_BOOT_READY_TIMEOUT)
        except (TimeoutError, asyncio.IncompleteReadError) as exc:
            process.kill()
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
            vsock_server.close()
            _cleanup_files(
                api_sock=api_sock,
                rootfs_copy=rootfs_copy,
                console_log=console_log,
                vsock_uds=vsock_uds,
            )
            raise SandboxCrashedError(
                f"firecracker instance {instance_id} never sent a real 'ready' "
                f"message within {_BOOT_READY_TIMEOUT}s -- see {console_log} for boot detail"
            ) from exc

        if ready_message.get("type") != "ready":
            process.kill()
            vsock_server.close()
            _cleanup_files(
                api_sock=api_sock,
                rootfs_copy=rootfs_copy,
                console_log=console_log,
                vsock_uds=vsock_uds,
            )
            raise SandboxCrashedError(
                f"firecracker instance {instance_id} sent an unexpected first "
                f"message instead of 'ready': {ready_message!r}"
            )

        self._instances[instance_id] = _InstanceState(
            api_sock=api_sock,
            vsock_uds=vsock_uds,
            rootfs_copy=rootfs_copy,
            console_log=console_log,
            process=process,
            reader=reader,
            writer=writer,
            vsock_server=vsock_server,
        )
        return SandboxHandle(id=instance_id, tier=2)

    async def execute(
        self,
        handle: SandboxHandle,
        code: str,
        *,
        on_tool_call: ToolCallCallback,
        timeout: float,
    ) -> RunResult:
        state = self._instances[handle.id]
        start_time = time.monotonic()
        tool_call_count = 0
        stdout_text = ""
        success = False
        error_message: str | None = None

        async def _run() -> None:
            nonlocal tool_call_count, stdout_text, success, error_message
            await _send(state.writer, {"type": "code", "code": code})
            while True:
                message = await _recv(state.reader)
                if message["type"] == "tool_call":
                    tool_call_count += 1
                    result = await on_tool_call(message["tool"], message["params"])
                    await _send(
                        state.writer,
                        {
                            "type": "tool_result",
                            "success": result["success"],
                            "value": result["value"],
                            "error_message": result["error_message"],
                        },
                    )
                elif message["type"] == "result":
                    stdout_text = message["stdout"]
                    success = message["success"]
                    error_message = message["error_message"]
                    return
                else:
                    raise SandboxCrashedError(
                        f"firecracker instance {handle.id} sent an unrecognized "
                        f"message type: {message.get('type')!r}"
                    )

        try:
            await asyncio.wait_for(_run(), timeout=timeout)
        except TimeoutError:
            raise SandboxTimeoutError(
                f"sandbox {handle.id} did not complete within {timeout}s"
            ) from None
        except (asyncio.IncompleteReadError, ConnectionError) as exc:
            raise SandboxCrashedError(
                f"firecracker instance {handle.id} closed its vsock connection "
                f"without reporting a result -- see {state.console_log} for detail"
            ) from exc

        duration_ms = (time.monotonic() - start_time) * 1000
        stdout_truncated = len(stdout_text.encode()) > MAX_STDOUT_BYTES
        if stdout_truncated:
            stdout_text = stdout_text.encode()[:MAX_STDOUT_BYTES].decode(errors="replace")

        return RunResult(
            success=success,
            stdout=stdout_text,
            stdout_truncated=stdout_truncated,
            error_message=error_message,
            # Real CPU-second accounting for a whole microVM (distinct from
            # a single child process's rusage) needs Firecracker's own
            # metrics API -- not wired up in this v1 pass; 0.0 is an
            # honest "not measured yet", not a silently wrong number.
            cpu_seconds=0.0,
            duration_ms=duration_ms,
            tool_call_count=tool_call_count,
        )

    async def terminate(self, handle: SandboxHandle) -> None:
        """Always terminates -- SandboxPool's own always-terminate-never-
        reuse rule, applied here exactly as it is for SubprocessSandbox:
        kills the real firecracker process, closes the vsock listener,
        and removes this instance's rootfs copy + socket files."""
        state = self._instances.pop(handle.id, None)
        if state is None:
            return
        with contextlib.suppress(ProcessLookupError):
            state.process.kill()
            await state.process.wait()
        state.vsock_server.close()
        with contextlib.suppress(Exception):
            state.writer.close()
        _cleanup_files(
            api_sock=state.api_sock,
            rootfs_copy=state.rootfs_copy,
            console_log=state.console_log,
            vsock_uds=state.vsock_uds,
        )

    async def health_check(self) -> bool:
        """A lightweight is-alive check, matching SubprocessSandbox's own
        intent for this method -- confirms the real binary/kernel/rootfs
        artifacts this backend depends on actually exist, never boots a
        real VM to check."""
        return (
            (
                shutil.which(self._firecracker_binary) is not None
                or Path(self._firecracker_binary).exists()
            )
            and Path(self._kernel_image_path).exists()
            and Path(self._base_rootfs_path).exists()
        )
