"""FirecrackerSandbox -- the Tier 2 self-hosted Sandbox backend
(isolation.md: microVM, own kernel, hardware-grade isolation).

Real, validated mechanism, not a guess: SPIKE-firecracker-boot-restore-latency.md
proved Firecracker's boot/snapshot/restore timing on real bare-metal hardware;
SPIKE-firecracker-vsock-callback-bridge.md proved a real, bidirectional vsock
round trip between a Firecracker guest running the ACTUAL
_firecracker_guest_shim.py and a real host-side listener, on the same
hardware -- confirmed with a real tool call crossing the VM boundary and a
real result returning.

DEFAULT: `boot_clean()` cold-boots, matching v1's original scope exactly --
no behavior change for any existing caller. `use_snapshot_restore=True`
(opt-in, PLAN.md item 20a) turns on real snapshot/restore instead, closing
SPIKE-firecracker-snapshot-restore-vsock-combination.md's own open
question: does a live vsock connection survive Firecracker snapshot/
restore into a fresh process? Verified on real hardware, not theorized --
see that spike doc and this class's own `use_snapshot_restore` docstring
for the full mechanism and its real, honestly-stated limits.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import struct
import time
import uuid
from pathlib import Path
from typing import Any

from fabrica.sandbox.errors import (
    SandboxConfigurationError,
    SandboxCrashedError,
    SandboxTimeoutError,
    SandboxToolCallTimeoutError,
)
from fabrica.sandbox.types import MAX_STDOUT_BYTES, RunResult, SandboxHandle, ToolCallCallback

_HOST_VSOCK_PORT = 5555  # matches _firecracker_guest_shim.py's own constant
_BOOT_READY_TIMEOUT = 10.0
"""Generous relative to the spike's measured ~1,055ms real-userspace-ready
figure -- real hardware/load variance shouldn't make a correct boot look
like a hang."""


def _read_process_cpu_seconds(pid: int) -> float:
    """Real, per-VM CPU time from the HOST's own view of the running
    firecracker process -- closes contracts/sandbox.md's own honestly-
    stated `cpu_seconds=0.0` gap. Firecracker's REST `/metrics` endpoint
    is write-only (configures a named pipe/file it periodically dumps
    operational counters to -- checked directly against the real,
    bundled `firecracker_spec-v1.16.1.yaml` OpenAPI spec, not assumed
    from memory), not a queryable CPU-seconds value -- so this reads
    `/proc/<pid>/stat` instead, the same mechanism `libvirt`/`virsh
    domstats` and most other VMM CPU-accounting layers use. Firecracker
    runs its vCPU(s) as threads WITHIN this one process, not as separate
    child processes, so this process's aggregate utime+stime already
    includes all real guest CPU execution -- verified for real on the
    homelab, not assumed: a CPU-bound guest loop measured ~2.81s of delta
    CPU time closely matching its ~2.81s wall-clock duration (a single
    vCPU, CPU-bound task), while a trivial `print(1)` measured ~0.000s.

    Returns 0.0 (not raising) if the process has already exited or
    `/proc` is unavailable for any other reason -- an honest "couldn't
    measure this time", the same posture the pre-existing `cpu_seconds
    =0.0` already held, never worse than before this existed.
    """
    try:
        with open(f"/proc/{pid}/stat") as f:
            # Command name (field 2, "(comm)") can itself contain spaces
            # and parentheses -- split from the LAST ")" onward, per
            # proc(5), rather than naively splitting the whole line on
            # whitespace from the start.
            fields = f.read().rsplit(")", 1)[1].split()
        # proc(5): field 14 is utime, field 15 is stime, 1-indexed overall
        # -- 0-indexed from AFTER the "(comm)" split, these are positions
        # 11 and 12 (14-1-2, 15-1-2, since fields 1 and 2 were consumed).
        utime_ticks = int(fields[11])
        stime_ticks = int(fields[12])
    except (OSError, IndexError, ValueError):
        return 0.0
    clock_ticks_per_second = os.sysconf("SC_CLK_TCK")
    return (utime_ticks + stime_ticks) / clock_ticks_per_second


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
        rootfs_copy: Path | None,
        console_log: Path,
        process: asyncio.subprocess.Process,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        vsock_server: asyncio.AbstractServer,
        jail_dir: Path | None = None,
    ) -> None:
        # rootfs_copy is None for a snapshot-restored instance -- it has
        # no per-instance rootfs copy of its own; restore always
        # references the GOLDEN rootfs file the snapshot embeds a fixed
        # path to (no override mechanism exists for it, unlike vsock's
        # own `vsock_override` -- confirmed against the real OpenAPI
        # spec), which terminate() must never delete underneath every
        # other restored instance still using it.
        self.api_sock = api_sock
        self.vsock_uds = vsock_uds
        self.rootfs_copy = rootfs_copy
        self.console_log = console_log
        self.process = process
        self.reader = reader
        self.writer = writer
        self.vsock_server = vsock_server
        # jail_dir is None for every non-jailed instance (cold-boot or
        # restored) -- set only for a real `use_jailer=True` instance,
        # to the exact per-jail directory
        # (<chroot_base_dir>/<exec_basename>/<instance_id>) the scoped
        # `fabrica-jailer-cleanup` sudoers rule permits removing.
        # terminate() dispatches on this field to decide which real
        # teardown path an instance needs -- a jailed process is a
        # separate, unprivileged child jailer detached from (killing the
        # sudo-invoked jailer monitor does NOT kill it, confirmed on real
        # hardware), and its on-disk footprint is fc-jail-owned by the
        # time it's done, not this process's own to delete directly.
        self.jail_dir = jail_dir


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
    *, api_sock: Path, rootfs_copy: Path | None, console_log: Path, vsock_uds: Path
) -> None:
    """Shared by every path that tears an instance down -- successful
    terminate() AND every boot_clean() failure branch. A real leak was
    found (and only partially fixed at first) by inspecting /tmp after
    real test runs, not assumed complete: boot_clean()'s own error paths
    left every one of these files behind, not just terminate()'s.

    `rootfs_copy=None` for a restored instance -- deliberately never
    deletes anything in that case, since a restored instance references
    the shared golden rootfs file, not a copy of its own (see
    `_InstanceState`'s own docstring).
    """
    paths = [api_sock, console_log, vsock_uds, Path(f"{vsock_uds}_{_HOST_VSOCK_PORT}")]
    if rootfs_copy is not None:
        paths.append(rootfs_copy)
    for path in paths:
        path.unlink(missing_ok=True)


async def _terminate_jailed_instance(
    *, instance_id: str, jail_dir: Path, console_log: Path | None = None
) -> None:
    """Real teardown for a `use_jailer=True` instance -- deliberately NOT
    `process.kill()` + `_cleanup_files()`, the non-jailed path's shape.
    Two real, confirmed-on-hardware findings drive this:

    1. `jailer`'s own process tree forks -- the process this backend
       invokes via `sudo` stays root-owned as a monitor, while the
       actual `firecracker` process is a SEPARATE child that fully drops
       to the jailed uid/gid. Killing the monitor does NOT kill that
       child (confirmed directly: it is orphaned instead). A scoped
       `pkill -u fc-jail -f` rule, bounded to the dedicated single-
       purpose `fc-jail` account and a real, stable substring of
       Firecracker's own argv shape, is required instead.
    2. By the time an instance is done, its whole jail directory is
       `fc-jail`-owned -- this process has no permission to remove it
       directly. A separate, narrowly-scoped `rm -rf` rule (matched via
       32 repetitions of the `[0-9a-f]` character class against
       `instance_id`, never a raw `*` wildcard, which sudoers' fnmatch-
       style globbing would treat as path-traversal-permissive) handles
       this.

    Both `sudo` calls are fire-and-forget from this method's own
    perspective on failure (best-effort, matching `_cleanup_files`'s own
    posture of never raising from a teardown path) -- a failed cleanup
    here is a real, but non-fatal, resource leak, not a correctness
    issue for the caller.
    """
    pkill = await asyncio.create_subprocess_exec(
        "sudo",
        "-n",
        "pkill",
        "-9",
        "-u",
        "fc-jail",
        "-f",
        "--",
        f"--id {instance_id} --start-time-us",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await pkill.wait()
    # Give the killed process a brief moment to actually exit before
    # removing its jail directory out from under it -- mirrors the
    # non-jailed path's own `await process.wait()` after kill().
    await asyncio.sleep(0.1)
    rm = await asyncio.create_subprocess_exec(
        "sudo",
        "-n",
        "rm",
        "-rf",
        str(jail_dir),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await rm.wait()
    if console_log is not None:
        console_log.unlink(missing_ok=True)


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


async def _api_patch(api_sock: Path, path: str, body: dict[str, Any]) -> None:
    """Same shape as `_api_put`, PATCH instead of PUT -- Firecracker's
    own REST API uses PATCH specifically for `/vm` state transitions
    (e.g. pausing before a snapshot), matching both spikes' real,
    validated command shape exactly.
    """
    proc = await asyncio.create_subprocess_exec(
        "curl",
        "-s",
        "-X",
        "PATCH",
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
        use_snapshot_restore: bool = False,
        use_jailer: bool = False,
        jailer_binary: str = "",
        jail_uid: int = 0,
        jail_gid: int = 0,
        chroot_base_dir: str = "/srv/jailer",
        stage_script: str = "",
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

        `use_snapshot_restore=False` (default) preserves v1's exact,
        original, tested behavior -- every `boot_clean()` cold-boots, no
        behavior change for any existing caller. `True` opts into real
        snapshot/restore instead, verified on real hardware
        (`SPIKE-firecracker-snapshot-restore-vsock-combination.md`): the
        FIRST `boot_clean()` call lazily cold-boots ONE throwaway
        instance purely to create a reusable "golden" snapshot (paying
        the real ~1s+ cold-boot cost exactly once, not per call), then
        every `boot_clean()` -- including that very first one -- restores
        a fresh instance from it in ~8-10ms instead. Requires the guest
        shim's own real reconnect logic (`_firecracker_guest_shim.py`),
        which a restored guest depends on to recover from the real,
        correct `ConnectionResetError` it gets on resume.

        **Real, honestly-stated limit, not silently assumed away**: every
        restored instance references the SAME golden rootfs file --
        Firecracker's `/snapshot/load` has a real `vsock_override` for
        giving each restored instance its own vsock path (verified
        working for concurrent instances), but no equivalent override
        exists for the block device (confirmed against the real,
        bundled OpenAPI spec). Verified SAFE for this project's own
        usage pattern specifically, not assumed: two concurrent restored
        instances each writing a distinct, deliberately-chosen file to
        their own guest filesystem, then reading it back, both
        completed correctly with no cross-contamination in this
        project's own real test -- the shared file backs each instance's
        OWN, already-loaded guest memory/page-cache state
        independently, not a live, continuously-shared block device in
        the way it would be for two normal VMs pointed at one disk
        image simultaneously. This holds specifically because every
        restored instance is used for exactly ONE `execute()` call then
        terminated (`SandboxPool`'s own always-terminate-never-reuse
        rule) -- nothing ever depends on the shared file's own on-disk
        state being consistent afterward. Revisit if a future use case
        ever needs a restored instance's disk writes to durably persist
        or be inspected after termination -- this design does not
        support that.

        `use_jailer=True` (default False, PLAN.md item 21) turns on real
        defense-in-depth hardening via Firecracker's own `jailer` binary
        -- chroot, cgroups, uid/gid drop -- on top of the existing KVM
        boundary. **Cold-boot only, by direct decision**: combining
        `use_jailer` with `use_snapshot_restore` raises
        `SandboxConfigurationError` at construction time -- a real,
        separate, harder combination deliberately never validated
        ("security over optimization"). Requires `jailer_binary`,
        `chroot_base_dir`, and `stage_script` to all be given together
        when `use_jailer=True` -- real, deployment-specific values this
        class receives, never derives itself. See
        `specs/archive/spikes/SPIKE-firecracker-jailer-vsock-integration.md`
        for the full, empirically-validated mechanism -- summarized here
        only where it affects this class's own behavior:

        - Kernel + a fresh per-instance rootfs copy are staged into the
          jail by `stage_script`, invoked via `sudo -n` against a real,
          narrowly-scoped sudoers rule -- not built by this class
          directly, since the jail's `root/` directory does not exist,
          and is not writable by this process, until the script
          (running as root) creates it.
        - The real hard problem this mechanism solves: `jailer` locks
          `root/` to `700 fc-jail:fc-jail` as part of its own setup, but
          the vsock host socket living inside it must stay usable by
          this (unprivileged) process. Fixed by binding +
          `chmod(0o777)`-ing that socket BEFORE invoking `jailer` (while
          `root/` is still writable), then relying on the already-open
          file descriptor surviving the subsequent lockdown -- a real,
          confirmed Unix property (permission checks happen at
          bind()/connect() time, not on every later operation via an
          already-open fd), not a weakening of the jail.
        - Boot configuration goes through Firecracker's own
          `--config-file` mechanism (a static JSON document written
          directly by this process before `jailer` runs), not the
          runtime REST API the non-jailed paths use -- the API socket is
          bound by `firecracker` itself, running as `fc-jail`, not by
          this process, so the vsock "bind before lockdown" trick does
          not apply to it. Confirmed this eliminates the need for any
          further sudo grant.
        - Termination uses a separate, scoped `pkill` rule, not
          `process.kill()`: `jailer`'s own process tree forks -- the
          process this class invokes via `sudo` stays root-owned as a
          monitor, while the actual `firecracker` process is a separate
          child that fully drops to the jailed uid/gid; killing the
          monitor does not kill that child (confirmed on real hardware --
          it is orphaned instead). Cleanup of the jail's on-disk
          footprint (potentially 1GB+ per instance) uses a third,
          separate, narrowly-scoped `rm` rule for the same reason -- by
          the time an instance is done, its whole jail directory is
          `fc-jail`-owned, not this process's own to delete directly.
        """
        self._firecracker_binary = firecracker_binary
        self._kernel_image_path = kernel_image_path
        self._base_rootfs_path = base_rootfs_path
        self._vcpu_count = vcpu_count
        self._mem_size_mib = mem_size_mib
        self._guest_shim_path = guest_shim_path
        self._socket_dir = Path(socket_dir)
        self._use_snapshot_restore = use_snapshot_restore
        self._use_jailer = use_jailer
        self._jailer_binary = jailer_binary
        self._jail_uid = jail_uid
        self._jail_gid = jail_gid
        self._chroot_base_dir = chroot_base_dir
        self._stage_script = stage_script
        if use_jailer and use_snapshot_restore:
            raise SandboxConfigurationError(
                "use_jailer=True and use_snapshot_restore=True cannot be combined -- "
                "this combination has deliberately never been validated (see "
                "specs/archive/spikes/SPIKE-firecracker-jailer-vsock-integration.md); "
                "jailer support is cold-boot only"
            )
        if use_jailer and not (jailer_binary and chroot_base_dir and stage_script):
            raise SandboxConfigurationError(
                "use_jailer=True requires jailer_binary, chroot_base_dir, and "
                "stage_script to all be given"
            )
        self._instances: dict[str, _InstanceState] = {}
        # Golden-snapshot state -- backend-INSTANCE-level, not per-handle
        # (Sandbox.close()'s own docstring names exactly this shape of
        # resource). Created lazily, once, protected by a lock so
        # concurrent boot_clean() calls racing on the very first request
        # don't each cold-boot their own throwaway instance.
        self._golden_snapshot_id = uuid.uuid4().hex[:8]
        self._golden_snapshot: tuple[Path, Path] | None = None  # (snap_state, snap_mem)
        self._golden_rootfs_copy: Path | None = None
        self._golden_snapshot_lock = asyncio.Lock()

    async def boot_clean(self) -> SandboxHandle:
        """Cold-boots by default (see `__init__`'s own docstring for the
        real reasoning); restores from a real, lazily-created snapshot
        instead when `use_snapshot_restore=True`.

        Raises:
            SandboxCrashedError: the guest never sent "ready" within
                _BOOT_READY_TIMEOUT.
        """
        if self._use_jailer:
            return await self._boot_jailed_instance()
        if self._use_snapshot_restore:
            await self._ensure_golden_snapshot()
            return await self._restore_instance()
        return await self._cold_boot_instance()

    async def _cold_boot_instance(self) -> SandboxHandle:
        """The exact, original v1 mechanism -- cold-boots a fresh guest,
        waits for the real guest-shim's "ready" message, proof of actual
        userspace readiness, not just Firecracker's own VMM=Running
        signal (the exact distinction SPIKE-firecracker-boot-restore-
        latency.md found matters). Used directly by `boot_clean()` when
        `use_snapshot_restore=False`, and internally by
        `_ensure_golden_snapshot()` to produce the one throwaway instance
        the golden snapshot is created from.
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

    async def _boot_jailed_instance(self) -> SandboxHandle:
        """Real `jailer`-hardened cold boot -- see `__init__`'s own
        docstring and
        `specs/archive/spikes/SPIKE-firecracker-jailer-vsock-integration.md`
        for the full, empirically-validated mechanism. Every step here
        mirrors a specific, real finding from that investigation; see
        inline comments for which.

        Uses the FULL 32-character `uuid4().hex` for `instance_id`, not
        the 8-character truncation the non-jailed paths use -- the real,
        already-installed `fabrica-jailer-cleanup` sudoers rule is
        pattern-matched against exactly 32 `[0-9a-f]` characters, and a
        shorter id would simply never match that grant.
        """
        instance_id = uuid.uuid4().hex
        exec_basename = Path(self._firecracker_binary).name
        jail_dir = Path(self._chroot_base_dir) / exec_basename / instance_id
        jail_root = jail_dir / "root"
        console_log = self._socket_dir / f"fc-{instance_id}-console.log"

        # 1. Stage kernel + a fresh per-instance rootfs copy into the
        #    jail -- must happen via the scoped script, running as root,
        #    since jail_root does not exist (and this process cannot
        #    create it) until this runs.
        stage_proc = await asyncio.create_subprocess_exec(
            "sudo",
            "-n",
            self._stage_script,
            self._chroot_base_dir,
            exec_basename,
            instance_id,
            self._kernel_image_path,
            self._base_rootfs_path,
            str(self._jail_uid),
            str(self._jail_gid),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stage_output, _ = await stage_proc.communicate()
        if stage_proc.returncode != 0:
            raise SandboxCrashedError(
                f"jailer staging failed for instance {instance_id}: "
                f"{stage_output.decode(errors='replace')}"
            )

        # 2. Write the boot configuration directly -- a plain,
        #    unprivileged file write. jail_root is still owned by this
        #    process at this point (the staging script deliberately
        #    leaves it that way, see stage_jailer_resources.sh's own
        #    header), so no privileged script is needed for this file.
        #    Real schema, confirmed against Firecracker v1.16.1's own
        #    source (src/vmm/src/resources.rs's VmmConfig struct) and
        #    empirically validated end to end, not assumed. boot_args
        #    must match the non-jailed paths' exact value -- omitting
        #    the init= override silently boots the rootfs's own default
        #    init instead of the guest shim, with no error signal at all
        #    (a real mistake made and caught during validation).
        boot_args = (
            f"console=ttyS0 reboot=k panic=1 init=/usr/bin/python3 -- {self._guest_shim_path}"
        )
        vm_config = {
            "boot-source": {"kernel_image_path": "/kernel", "boot_args": boot_args},
            "drives": [
                {
                    "drive_id": "rootfs",
                    "path_on_host": "/rootfs.ext4",
                    "is_root_device": True,
                    "is_read_only": False,
                }
            ],
            "machine-config": {
                "vcpu_count": self._vcpu_count,
                "mem_size_mib": self._mem_size_mib,
            },
            "vsock": {"guest_cid": 3, "uds_path": "/vsock.sock"},
        }
        (jail_root / "vm-config.json").write_text(json.dumps(vm_config))

        # 3. Bind + listen the vsock socket INSIDE jail_root, BEFORE
        #    jailer runs, while jail_root is still writable by this
        #    process -- the real fix for the hard problem this whole
        #    mechanism exists to solve (see __init__'s own docstring).
        #    Explicit chmod(0o777): Python's default socket.bind()
        #    leaves "other" without write access, and fc-jail (once
        #    jailer locks jail_root down) is evaluated as "other" for
        #    this file -- confirmed to be the actual, only real bug
        #    behind every earlier vsock failure, not a fundamental wall.
        vsock_uds = jail_root / "vsock.sock"
        vsock_sock_path = f"{vsock_uds}_{_HOST_VSOCK_PORT}"

        vsock_connection: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
            asyncio.get_event_loop().create_future()
        )

        async def _accept_guest_connection(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            if not vsock_connection.done():
                vsock_connection.set_result((reader, writer))

        vsock_server = await asyncio.start_unix_server(
            _accept_guest_connection, path=vsock_sock_path
        )
        os.chmod(vsock_sock_path, 0o777)

        # 4. Invoke jailer -- root/ locks down to fc-jail:fc-jail from
        #    this point on. No --daemonize: it would have to appear
        #    BEFORE the trailing `--`, which would break the exact,
        #    already-approved sudoers pattern match; not needed anyway,
        #    since without it jailer just exec()s straight into
        #    firecracker in the foreground (same PID) -- a non-blocking
        #    process handle achieves the same effect. Boot goes entirely
        #    through --config-file, no runtime API calls -- the API
        #    socket is unreachable to this process once root/ is locked
        #    (bound by firecracker itself as fc-jail, not by this
        #    process, so the vsock "bind before lockdown" trick does not
        #    apply to it -- confirmed --config-file avoids needing it at
        #    all).
        process = await asyncio.create_subprocess_exec(
            "sudo",
            "-n",
            self._jailer_binary,
            "--id",
            instance_id,
            "--exec-file",
            self._firecracker_binary,
            "--uid",
            str(self._jail_uid),
            "--gid",
            str(self._jail_gid),
            "--chroot-base-dir",
            self._chroot_base_dir,
            "--",
            "--config-file",
            "/vm-config.json",
            "--no-api",
            stdout=open(console_log, "wb"),  # noqa: SIM115 -- lifetime matches the subprocess, not this scope
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            reader, writer = await asyncio.wait_for(vsock_connection, timeout=_BOOT_READY_TIMEOUT)
            ready_message = await asyncio.wait_for(_recv(reader), timeout=_BOOT_READY_TIMEOUT)
        except (TimeoutError, asyncio.IncompleteReadError) as exc:
            vsock_server.close()
            await _terminate_jailed_instance(
                instance_id=instance_id, jail_dir=jail_dir, console_log=console_log
            )
            raise SandboxCrashedError(
                f"jailed firecracker instance {instance_id} never sent a real "
                f"'ready' message within {_BOOT_READY_TIMEOUT}s -- see {console_log} "
                f"for boot detail"
            ) from exc

        if ready_message.get("type") != "ready":
            vsock_server.close()
            await _terminate_jailed_instance(
                instance_id=instance_id, jail_dir=jail_dir, console_log=console_log
            )
            raise SandboxCrashedError(
                f"jailed firecracker instance {instance_id} sent an unexpected first "
                f"message instead of 'ready': {ready_message!r}"
            )

        self._instances[instance_id] = _InstanceState(
            api_sock=Path(""),  # no API socket in --no-api jailed mode -- never used
            vsock_uds=vsock_uds,
            rootfs_copy=None,  # lives inside jail_dir, removed by _terminate_jailed_instance
            console_log=console_log,
            process=process,
            reader=reader,
            writer=writer,
            vsock_server=vsock_server,
            jail_dir=jail_dir,
        )
        return SandboxHandle(id=instance_id, tier=2)

    async def _ensure_golden_snapshot(self) -> None:
        """Lazily creates the reusable golden snapshot every restore
        reads from, exactly once, no matter how many concurrent
        `boot_clean()` calls race to be first (double-checked locking --
        the cheap unlocked check first, then re-checked once inside the
        lock, so only the very first caller ever actually pays for this).
        """
        if self._golden_snapshot is not None:
            return
        async with self._golden_snapshot_lock:
            if self._golden_snapshot is not None:
                return

            handle = await self._cold_boot_instance()
            state = self._instances.pop(handle.id)

            snap_state = self._socket_dir / f"fc-golden-{self._golden_snapshot_id}.state"
            snap_mem = self._socket_dir / f"fc-golden-{self._golden_snapshot_id}.mem"
            snap_state.unlink(missing_ok=True)
            snap_mem.unlink(missing_ok=True)

            await _api_patch(state.api_sock, "/vm", {"state": "Paused"})
            await _api_put(
                state.api_sock,
                "/snapshot/create",
                {"snapshot_path": str(snap_state), "mem_file_path": str(snap_mem)},
            )

            # Tear this throwaway instance down WITHOUT deleting its
            # rootfs copy -- the snapshot's own state now embeds a fixed
            # reference to that exact file's path, which every future
            # restore depends on (see __init__'s own docstring for why
            # there's no override for this, unlike vsock's real one).
            state.process.kill()
            with contextlib.suppress(ProcessLookupError):
                await state.process.wait()
            state.vsock_server.close()
            with contextlib.suppress(Exception):
                state.writer.close()
            _cleanup_files(
                api_sock=state.api_sock,
                rootfs_copy=None,  # deliberately NOT deleted -- see above
                console_log=state.console_log,
                vsock_uds=state.vsock_uds,
            )

            self._golden_rootfs_copy = state.rootfs_copy
            self._golden_snapshot = (snap_state, snap_mem)

    async def _restore_instance(self) -> SandboxHandle:
        """Restores a fresh instance from the already-created golden
        snapshot -- real, measured ~8-10ms on the homelab, vs. the
        cold-boot path's ~1s+. Every restored instance gets its OWN
        vsock path via Firecracker's real `vsock_override` (verified
        working for concurrent instances on real hardware) -- but shares
        the ONE golden rootfs file with every other restored instance
        (see `__init__`'s own docstring for why that's a real, accepted
        limit, not an oversight).
        """
        assert self._golden_snapshot is not None
        snap_state, snap_mem = self._golden_snapshot

        instance_id = uuid.uuid4().hex[:8]
        api_sock = self._socket_dir / f"fc-{instance_id}-api.sock"
        vsock_uds = self._socket_dir / f"fc-{instance_id}-vsock.sock"
        console_log = self._socket_dir / f"fc-{instance_id}-console.log"

        vsock_connection: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
            asyncio.get_event_loop().create_future()
        )

        async def _accept_guest_connection(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            if not vsock_connection.done():
                vsock_connection.set_result((reader, writer))

        # The fresh listener must exist BEFORE snapshot/load resumes the
        # guest -- exactly the ordering SPIKE-firecracker-snapshot-
        # restore-vsock-combination.md's own successful run depended on.
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
        for _ in range(100):
            if api_sock.exists():
                break
            await asyncio.sleep(0.01)

        await _api_put(
            api_sock,
            "/snapshot/load",
            {
                "snapshot_path": str(snap_state),
                "mem_backend": {"backend_type": "File", "backend_path": str(snap_mem)},
                "resume_vm": True,
                "vsock_override": {"uds_path": str(vsock_uds)},
            },
        )

        try:
            reader, writer = await asyncio.wait_for(vsock_connection, timeout=_BOOT_READY_TIMEOUT)
            ready_message = await asyncio.wait_for(_recv(reader), timeout=_BOOT_READY_TIMEOUT)
        except (TimeoutError, asyncio.IncompleteReadError) as exc:
            process.kill()
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
            vsock_server.close()
            _cleanup_files(
                api_sock=api_sock, rootfs_copy=None, console_log=console_log, vsock_uds=vsock_uds
            )
            raise SandboxCrashedError(
                f"firecracker instance {instance_id} (restored) never sent a real "
                f"'ready' message within {_BOOT_READY_TIMEOUT}s -- see {console_log} "
                f"for boot detail"
            ) from exc

        if ready_message.get("type") != "ready":
            process.kill()
            vsock_server.close()
            _cleanup_files(
                api_sock=api_sock, rootfs_copy=None, console_log=console_log, vsock_uds=vsock_uds
            )
            raise SandboxCrashedError(
                f"firecracker instance {instance_id} (restored) sent an unexpected "
                f"first message instead of 'ready': {ready_message!r}"
            )

        self._instances[instance_id] = _InstanceState(
            api_sock=api_sock,
            vsock_uds=vsock_uds,
            rootfs_copy=None,  # shares the golden rootfs -- never this instance's to delete
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
        tool_call_timeout: float | None = None,
    ) -> RunResult:
        state = self._instances[handle.id]
        start_time = time.monotonic()
        start_cpu_seconds = _read_process_cpu_seconds(state.process.pid)
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
                    if tool_call_timeout is not None:
                        try:
                            result = await asyncio.wait_for(
                                on_tool_call(message["tool"], message["params"]),
                                timeout=tool_call_timeout,
                            )
                        except TimeoutError as exc:
                            raise SandboxToolCallTimeoutError(
                                f"firecracker instance {handle.id}: tool call "
                                f"{message['tool']!r} did not complete within "
                                f"{tool_call_timeout}s"
                            ) from exc
                    else:
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

        # Real, per-call CPU-second accounting -- a DELTA against this
        # same process's own cumulative /proc counter, not a lifetime
        # total, matching the same "delta, not high-water-mark" discipline
        # already applied to Sandbox's memory-bytes dimension (deliberately
        # left unmeasured there because RUSAGE_CHILDREN's ru_maxrss is a
        # lifetime high-water-mark, not a per-call figure -- this metric
        # doesn't have that problem, since /proc's utime+stime genuinely
        # accumulates monotonically and a delta against it is real).
        # max(0.0, ...) guards the rare case where the process already
        # exited between the start read and here (both reads then return
        # 0.0, so this is defensive, not expected to fire in practice).
        cpu_seconds = max(0.0, _read_process_cpu_seconds(state.process.pid) - start_cpu_seconds)

        return RunResult(
            success=success,
            stdout=stdout_text,
            stdout_truncated=stdout_truncated,
            error_message=error_message,
            cpu_seconds=cpu_seconds,
            duration_ms=duration_ms,
            tool_call_count=tool_call_count,
        )

    async def terminate(self, handle: SandboxHandle) -> None:
        """Always terminates -- SandboxPool's own always-terminate-never-
        reuse rule, applied here exactly as it is for SubprocessSandbox:
        kills the real firecracker process, closes the vsock listener,
        and removes this instance's rootfs copy + socket files.

        Dispatches on `state.jail_dir` -- a jailed instance's real
        `firecracker` process is a separate child `jailer`'s own process
        tree forks off, fully dropped to an unprivileged uid/gid;
        `process.kill()` here would only kill the root-owned `sudo`/
        `jailer` monitor, leaving that child orphaned (confirmed on real
        hardware) -- see `_terminate_jailed_instance`'s own docstring for
        the full, real reasoning.
        """
        state = self._instances.pop(handle.id, None)
        if state is None:
            return
        state.vsock_server.close()
        with contextlib.suppress(Exception):
            state.writer.close()
        if state.jail_dir is not None:
            await _terminate_jailed_instance(
                instance_id=handle.id, jail_dir=state.jail_dir, console_log=state.console_log
            )
            return
        with contextlib.suppress(ProcessLookupError):
            state.process.kill()
            await state.process.wait()
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
        real VM to check. Also confirms `jailer_binary`/`stage_script`
        exist when `use_jailer=True` -- the same real artifacts
        `_boot_jailed_instance` depends on."""
        base_ok = (
            (
                shutil.which(self._firecracker_binary) is not None
                or Path(self._firecracker_binary).exists()
            )
            and Path(self._kernel_image_path).exists()
            and Path(self._base_rootfs_path).exists()
        )
        if not self._use_jailer:
            return base_ok
        return (
            base_ok
            and (
                shutil.which(self._jailer_binary) is not None or Path(self._jailer_binary).exists()
            )
            and Path(self._stage_script).exists()
        )

    async def close(self) -> None:
        """A genuine no-op when `use_snapshot_restore=False` (the
        default) -- every real resource this backend owns in that mode
        (rootfs copy, sockets, console log, the running process) is
        per-instance-id and already torn down by terminate(). See
        Sandbox.close()'s own docstring for why this method exists.

        When `use_snapshot_restore=True`, the golden snapshot's own
        files (state, memory, and its shared rootfs copy) ARE real
        backend-instance-level resources -- exactly the shape
        `Sandbox.close()` exists to release, never owned by any single
        `SandboxHandle`/`terminate()` call. Safe to call even if no
        `boot_clean()` was ever made (the golden snapshot may not exist
        yet) -- matches every other backend's `close()` contract.
        """
        if self._golden_snapshot is not None:
            snap_state, snap_mem = self._golden_snapshot
            snap_state.unlink(missing_ok=True)
            snap_mem.unlink(missing_ok=True)
            self._golden_snapshot = None
        if self._golden_rootfs_copy is not None:
            self._golden_rootfs_copy.unlink(missing_ok=True)
            self._golden_rootfs_copy = None
