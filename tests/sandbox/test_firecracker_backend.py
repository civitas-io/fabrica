"""Tests for FirecrackerSandbox -- REAL Firecracker microVMs, real vsock,
real tool calls crossing a real VM boundary. Not mocked anywhere in this
path. Requires actual Firecracker + KVM (Linux only) -- skipped everywhere
else, same discipline as the srt-sandboxed MCP tests.

Requires environment variables pointing at real, pre-built artifacts (a
kernel image, a rootfs with _firecracker_guest_shim.py already baked in at
/tmp/guest_shim.py -- see SPIKE-firecracker-vsock-callback-bridge.md for
exactly how that's built):

    FABRICA_FC_BINARY, FABRICA_FC_KERNEL, FABRICA_FC_ROOTFS
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from fabrica.sandbox import SandboxCrashedError, SandboxTimeoutError, SandboxToolCallTimeoutError
from fabrica.sandbox.firecracker_backend import FirecrackerSandbox

_FC_BINARY = os.environ.get("FABRICA_FC_BINARY", "")
_FC_KERNEL = os.environ.get("FABRICA_FC_KERNEL", "")
_FC_ROOTFS = os.environ.get("FABRICA_FC_ROOTFS", "")

_REAL_ENV_AVAILABLE = (
    bool(_FC_BINARY)
    and bool(_FC_KERNEL)
    and bool(_FC_ROOTFS)
    and (shutil.which(_FC_BINARY) is not None or Path(_FC_BINARY).exists())
    and Path(_FC_KERNEL).exists()
    and Path(_FC_ROOTFS).exists()
    and Path("/dev/kvm").exists()
)

pytestmark = pytest.mark.skipif(
    not _REAL_ENV_AVAILABLE,
    reason="requires real Firecracker + KVM + FABRICA_FC_* env vars (Linux only)",
)


async def _no_tool_calls(tool: str, params: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError(f"unexpected tool call: {tool}({params})")


@pytest.fixture
def backend() -> FirecrackerSandbox:
    return FirecrackerSandbox(
        firecracker_binary=_FC_BINARY,
        kernel_image_path=_FC_KERNEL,
        base_rootfs_path=_FC_ROOTFS,
    )


async def test_health_check_true_when_artifacts_present(backend: FirecrackerSandbox) -> None:
    assert await backend.health_check() is True


async def test_close_is_a_safe_no_op(backend: FirecrackerSandbox) -> None:
    # Tier 2 allocates no instance-level resource of its own -- every real
    # resource is per-instance-id and already torn down by terminate().
    await backend.close()  # must not raise


async def test_health_check_false_when_kernel_missing() -> None:
    backend = FirecrackerSandbox(
        firecracker_binary=_FC_BINARY,
        kernel_image_path="/no/such/kernel",
        base_rootfs_path=_FC_ROOTFS,
    )
    assert await backend.health_check() is False


async def test_boot_clean_returns_tier_2_handle(backend: FirecrackerSandbox) -> None:
    handle = await backend.boot_clean()
    try:
        assert handle.tier == 2
        assert handle.id
    finally:
        await backend.terminate(handle)


async def test_execute_captures_real_stdout_no_tool_calls(backend: FirecrackerSandbox) -> None:
    handle = await backend.boot_clean()
    try:
        result = await backend.execute(
            handle, "print('hello from a real microVM')", on_tool_call=_no_tool_calls, timeout=15.0
        )
        assert result.success is True
        assert result.stdout.strip() == "hello from a real microVM"
        assert result.tool_call_count == 0
    finally:
        await backend.terminate(handle)


async def test_execute_real_tool_call_round_trip_across_the_vm_boundary(
    backend: FirecrackerSandbox,
) -> None:
    """The real proof: generated code calls namespace.call(), which crosses
    a REAL Firecracker VM boundary over vsock, into an ACTUAL Python
    function running on the host -- nothing mocked in this path.
    """

    async def add_tool(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        assert tool == "add"
        return {"success": True, "value": params["a"] + params["b"], "error_message": None}

    handle = await backend.boot_clean()
    try:
        code = (
            "result = namespace.call('add', {'a': 2, 'b': 3})\n"
            "print(f'2 + 3 = {result[\"value\"]}')\n"
        )
        result = await backend.execute(handle, code, on_tool_call=add_tool, timeout=15.0)
        assert result.success is True
        assert result.stdout.strip() == "2 + 3 = 5"
        assert result.tool_call_count == 1
    finally:
        await backend.terminate(handle)


async def test_execute_reports_code_level_failure_as_routine_result(
    backend: FirecrackerSandbox,
) -> None:
    handle = await backend.boot_clean()
    try:
        result = await backend.execute(
            handle,
            "raise ValueError('deliberate failure')",
            on_tool_call=_no_tool_calls,
            timeout=15.0,
        )
        assert result.success is False
        assert result.error_message is not None
        assert "deliberate failure" in result.error_message
    finally:
        await backend.terminate(handle)


async def test_execute_raises_timeout_error_and_kills_process(backend: FirecrackerSandbox) -> None:
    handle = await backend.boot_clean()
    try:
        with pytest.raises(SandboxTimeoutError):
            await backend.execute(
                handle,
                "import time; time.sleep(30)",
                on_tool_call=_no_tool_calls,
                timeout=1.0,
            )
    finally:
        await backend.terminate(handle)


async def test_tool_call_timeout_fires_before_the_overall_timeout(
    backend: FirecrackerSandbox,
) -> None:
    """contracts/sandbox.md open item 3, resolved: a hung tool call is
    caught by tool_call_timeout specifically (SandboxToolCallTimeoutError,
    not a generic SandboxTimeoutError), well before the much larger
    overall timeout budget would have caught it -- real, on real hardware,
    same as the SubprocessSandbox proof.
    """
    import time as _time

    async def hangs_forever(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {"unreachable": True}

    handle = await backend.boot_clean()
    try:
        start = _time.monotonic()
        with pytest.raises(SandboxToolCallTimeoutError):
            await backend.execute(
                handle,
                "namespace.call('slow_tool', {})",
                on_tool_call=hangs_forever,
                timeout=30.0,
                tool_call_timeout=0.5,
            )
        elapsed = _time.monotonic() - start
        assert elapsed < 10.0
    finally:
        await backend.terminate(handle)


async def test_terminate_is_safe_to_call_twice(backend: FirecrackerSandbox) -> None:
    handle = await backend.boot_clean()
    await backend.terminate(handle)
    await backend.terminate(handle)  # must not raise


async def test_terminate_removes_every_file_it_created(backend: FirecrackerSandbox) -> None:
    # A real leak was found here by inspecting /tmp after a real test run --
    # terminate() was cleaning up the {vsock_uds}_{port} guest-connection
    # proxy socket, but not vsock_uds itself (Firecracker's own separate
    # control path for the vsock device). This test exists specifically so
    # that regression can't silently reappear.
    handle = await backend.boot_clean()
    await backend.terminate(handle)
    leftover = list(Path("/tmp").glob(f"fc-{handle.id}-*"))
    assert leftover == [], f"terminate() left files behind: {leftover}"


async def test_execute_has_no_network_path_at_all(backend: FirecrackerSandbox) -> None:
    """The real proof behind isolation.md's design claim (vsock "without
    exposing IP networking") -- FirecrackerSandbox never calls
    Firecracker's own `/network-interfaces` API, so the guest boots with
    NO network device at all, not a policy-filtered one. This is
    structurally stronger than SrtSandbox's allow-only enforcement (there
    is nothing to misconfigure, no policy to bypass), but until this
    test, it had never actually been verified end to end -- only implied
    by the absence of code, matching the honest gap named in
    contracts/sandbox.md.

    A guest with no network device fails a connection attempt
    immediately (ENETUNREACH / no route), not by timing out -- unlike
    srt's own OS-level policy enforcement, which still has an interface
    to route through and denies at the firewall/proxy layer instead.
    """
    handle = await backend.boot_clean()
    try:
        code = (
            "import socket\n"
            "try:\n"
            "    s = socket.create_connection(('8.8.8.8', 53), timeout=5)\n"
            "    s.close()\n"
            "    print('REACHED')\n"
            "except Exception as exc:\n"
            "    print('BLOCKED', type(exc).__name__, str(exc))\n"
        )
        result = await backend.execute(handle, code, on_tool_call=_no_tool_calls, timeout=15.0)

        assert result.success is True, result.error_message
        assert "REACHED" not in result.stdout
        assert "BLOCKED" in result.stdout
    finally:
        await backend.terminate(handle)


async def test_execute_dns_resolution_also_has_no_path(backend: FirecrackerSandbox) -> None:
    """Same proof, at the DNS layer specifically -- a guest that could
    somehow still resolve names (e.g. via a cached /etc/hosts trick) but
    not connect would be a different, narrower claim than "no network
    path at all". Both must fail the same way: no route, immediately.
    """
    handle = await backend.boot_clean()
    try:
        code = (
            "import socket\n"
            "try:\n"
            "    socket.gethostbyname('example.com')\n"
            "    print('RESOLVED')\n"
            "except Exception as exc:\n"
            "    print('BLOCKED', type(exc).__name__, str(exc))\n"
        )
        result = await backend.execute(handle, code, on_tool_call=_no_tool_calls, timeout=15.0)

        assert result.success is True, result.error_message
        assert "RESOLVED" not in result.stdout
        assert "BLOCKED" in result.stdout
    finally:
        await backend.terminate(handle)


async def test_execute_reports_real_positive_cpu_seconds_for_a_cpu_bound_task(
    backend: FirecrackerSandbox,
) -> None:
    """contracts/sandbox.md's own honestly-stated cpu_seconds=0.0 gap,
    resolved: real, per-call CPU time measured from the host's own view
    of the running firecracker process (/proc/<pid>/stat), not a
    fabricated or copy-pasted number. A CPU-bound loop must show real,
    non-trivial CPU time -- not just "greater than zero" (a rounding
    artifact could satisfy that trivially), but close to the task's own
    measured wall-clock duration, since a single-vCPU, single-threaded,
    CPU-bound guest task should consume close to 1 CPU-second per
    wall-clock second.
    """
    handle = await backend.boot_clean()
    try:
        code = "total = 0\nfor i in range(30_000_000):\n    total += i * i\nprint(total)\n"
        result = await backend.execute(handle, code, on_tool_call=_no_tool_calls, timeout=30.0)

        assert result.success is True, result.error_message
        assert result.cpu_seconds > 0.5  # real hardware measured ~2.8s for this exact workload
        # Within the same order of magnitude as wall-clock duration -- a
        # single-vCPU CPU-bound task can't consume MORE CPU time than
        # wall-clock time elapsed, with a little slack for measurement
        # granularity (SC_CLK_TCK is commonly 100Hz, i.e. 10ms buckets).
        assert result.cpu_seconds <= (result.duration_ms / 1000) + 0.5
    finally:
        await backend.terminate(handle)


async def test_execute_reports_near_zero_cpu_seconds_for_a_trivial_task(
    backend: FirecrackerSandbox,
) -> None:
    handle = await backend.boot_clean()
    try:
        result = await backend.execute(
            handle, "print(1)", on_tool_call=_no_tool_calls, timeout=15.0
        )

        assert result.success is True, result.error_message
        # Real hardware measured 0.000s for this exact workload -- a
        # generous ceiling here, not a tight bound, since this is about
        # proving "a trivial task doesn't report a large bogus number",
        # not pinning an exact figure that real hardware variance could
        # break.
        assert result.cpu_seconds < 0.5
    finally:
        await backend.terminate(handle)


async def test_boot_clean_raises_crashed_error_for_a_bad_kernel_path() -> None:
    backend = FirecrackerSandbox(
        firecracker_binary=_FC_BINARY,
        kernel_image_path="/no/such/kernel",
        base_rootfs_path=_FC_ROOTFS,
    )
    with pytest.raises(SandboxCrashedError):
        await backend.boot_clean()


# ---------------------------------------------------------------------------
# use_snapshot_restore=True -- real snapshot/restore, closing PLAN.md item
# 20a. SPIKE-firecracker-snapshot-restore-vsock-combination.md validated the
# mechanism with a throwaway patched shim; these tests validate the REAL,
# shipped implementation (the actual _firecracker_guest_shim.py, the actual
# FirecrackerSandbox), not the spike's disposable copy.
# ---------------------------------------------------------------------------


@pytest.fixture
def snapshot_backend() -> FirecrackerSandbox:
    return FirecrackerSandbox(
        firecracker_binary=_FC_BINARY,
        kernel_image_path=_FC_KERNEL,
        base_rootfs_path=_FC_ROOTFS,
        use_snapshot_restore=True,
    )


async def test_snapshot_restore_produces_a_real_working_tier_2_handle(
    snapshot_backend: FirecrackerSandbox,
) -> None:
    handle = await snapshot_backend.boot_clean()
    try:
        assert handle.tier == 2
        result = await snapshot_backend.execute(
            handle,
            "print('hello from a restored microVM')",
            on_tool_call=_no_tool_calls,
            timeout=15.0,
        )
        assert result.success is True, result.error_message
        assert result.stdout.strip() == "hello from a restored microVM"
    finally:
        await snapshot_backend.terminate(handle)
        await snapshot_backend.close()


async def test_second_boot_clean_is_dramatically_faster_than_the_first(
    snapshot_backend: FirecrackerSandbox,
) -> None:
    """The real point of this feature -- proves restore is actually
    happening for the SECOND call, not just "it works", by measuring a
    real, large latency gap: the first call pays the real cold-boot cost
    (creating the golden snapshot), the second restores in single/low-
    double-digit ms. Real hardware measured ~1.9s cold-boot-and-snapshot
    vs. ~10ms restore in the underlying spike -- this asserts a
    conservative, much looser ratio (at least 5x), not that exact figure,
    to stay robust against real hardware variance.
    """
    import time

    t0 = time.monotonic()
    handle1 = await snapshot_backend.boot_clean()
    first_ms = (time.monotonic() - t0) * 1000

    t0 = time.monotonic()
    handle2 = await snapshot_backend.boot_clean()
    second_ms = (time.monotonic() - t0) * 1000

    try:
        assert second_ms < first_ms / 5
    finally:
        await snapshot_backend.terminate(handle1)
        await snapshot_backend.terminate(handle2)
        await snapshot_backend.close()


async def test_concurrent_restored_instances_are_independently_isolated(
    snapshot_backend: FirecrackerSandbox,
) -> None:
    """The real safety proof for sharing one golden rootfs file across
    concurrent restored instances (see FirecrackerSandbox.__init__'s own
    docstring for the full reasoning): two instances, each writing a
    DIFFERENT, distinguishable value to their own guest filesystem
    concurrently, must each read back their OWN correct value -- no
    cross-contamination.
    """
    handle_a, handle_b = await asyncio.gather(
        snapshot_backend.boot_clean(), snapshot_backend.boot_clean()
    )
    try:

        async def _write_and_read_back(handle: Any, marker: str) -> str:
            code = (
                f"with open('/tmp/marker.txt', 'w') as f:\n"
                f"    f.write('{marker}' * 100)\n"
                f"with open('/tmp/marker.txt') as f:\n"
                f"    print(f.read())\n"
            )
            result = await snapshot_backend.execute(
                handle, code, on_tool_call=_no_tool_calls, timeout=15.0
            )
            assert result.success is True, result.error_message
            return result.stdout.strip()

        stdout_a, stdout_b = await asyncio.gather(
            _write_and_read_back(handle_a, "a"), _write_and_read_back(handle_b, "b")
        )
        assert stdout_a == "a" * 100
        assert stdout_b == "b" * 100
    finally:
        await snapshot_backend.terminate(handle_a)
        await snapshot_backend.terminate(handle_b)
        await snapshot_backend.close()


async def test_restored_instance_real_tool_call_round_trip(
    snapshot_backend: FirecrackerSandbox,
) -> None:
    handle = await snapshot_backend.boot_clean()
    try:

        async def add_tool(tool: str, params: dict[str, Any]) -> dict[str, Any]:
            return {"success": True, "value": params["a"] + params["b"], "error_message": None}

        code = (
            "result = namespace.call('add', {'a': 2, 'b': 3})\n"
            "print(f'2 + 3 = {result[\"value\"]}')\n"
        )
        result = await snapshot_backend.execute(handle, code, on_tool_call=add_tool, timeout=15.0)
        assert result.success is True
        assert result.stdout.strip() == "2 + 3 = 5"
        assert result.tool_call_count == 1
    finally:
        await snapshot_backend.terminate(handle)
        await snapshot_backend.close()


async def test_terminate_on_a_restored_instance_does_not_delete_the_shared_golden_rootfs(
    snapshot_backend: FirecrackerSandbox,
) -> None:
    """Real regression guard for the exact bug this design deliberately
    avoids: a restored instance's rootfs_copy is None (it shares the
    golden rootfs, never owns a copy of its own) -- terminate() must
    never delete the golden rootfs file underneath any other instance
    still using it.
    """
    handle_a = await snapshot_backend.boot_clean()
    await snapshot_backend.terminate(handle_a)

    # A SECOND restore must still work after the first instance's
    # terminate() -- proves the golden rootfs survived.
    handle_b = await snapshot_backend.boot_clean()
    try:
        result = await snapshot_backend.execute(
            handle_b, "print('still alive')", on_tool_call=_no_tool_calls, timeout=15.0
        )
        assert result.success is True
        assert result.stdout.strip() == "still alive"
    finally:
        await snapshot_backend.terminate(handle_b)
        await snapshot_backend.close()


async def test_close_removes_the_golden_snapshot_files(
    snapshot_backend: FirecrackerSandbox,
) -> None:
    handle = await snapshot_backend.boot_clean()
    await snapshot_backend.terminate(handle)

    assert snapshot_backend._golden_snapshot is not None
    snap_state, snap_mem = snapshot_backend._golden_snapshot
    golden_rootfs = snapshot_backend._golden_rootfs_copy
    assert snap_state.exists()
    assert snap_mem.exists()
    assert golden_rootfs is not None and golden_rootfs.exists()

    await snapshot_backend.close()

    assert not snap_state.exists()
    assert not snap_mem.exists()
    assert not golden_rootfs.exists()


async def test_close_is_safe_when_no_golden_snapshot_was_ever_created() -> None:
    backend = FirecrackerSandbox(
        firecracker_binary=_FC_BINARY,
        kernel_image_path=_FC_KERNEL,
        base_rootfs_path=_FC_ROOTFS,
        use_snapshot_restore=True,
    )
    await backend.close()  # must not raise -- no boot_clean() was ever called


async def test_use_snapshot_restore_false_by_default_preserves_cold_boot_behavior(
    backend: FirecrackerSandbox,
) -> None:
    # backend fixture (module-level, above) has use_snapshot_restore
    # unset -- must still cold-boot exactly as before, no behavior change.
    handle = await backend.boot_clean()
    try:
        assert backend._golden_snapshot is None  # never touched
        result = await backend.execute(
            handle, "print('cold boot unchanged')", on_tool_call=_no_tool_calls, timeout=15.0
        )
        assert result.success is True
        assert result.stdout.strip() == "cold boot unchanged"
    finally:
        await backend.terminate(handle)


# ---------------------------------------------------------------------------
# use_jailer=True -- real defense-in-depth hardening, PLAN.md item 21.
# Fully validated on the homelab in
# specs/archive/spikes/SPIKE-firecracker-jailer-vsock-integration.md before
# this real implementation was written -- see that doc for the full,
# empirically-confirmed mechanism (config-file schema, bind-before-lockdown
# vsock trick, the three narrowly-scoped sudoers rules) these tests exercise
# against the actual shipped code, not a throwaway spike script.
# ---------------------------------------------------------------------------

_JAILER_BINARY = os.environ.get("FABRICA_FC_JAILER", "")
_STAGE_SCRIPT = os.environ.get("FABRICA_FC_STAGE_SCRIPT", "")
_CHROOT_BASE_DIR = os.environ.get("FABRICA_FC_CHROOT_BASE_DIR", "/srv/jailer")
_JAIL_UID = int(os.environ.get("FABRICA_FC_JAIL_UID", "0") or "0")
_JAIL_GID = int(os.environ.get("FABRICA_FC_JAIL_GID", "0") or "0")

_JAILER_ENV_AVAILABLE = (
    _REAL_ENV_AVAILABLE
    and bool(_JAILER_BINARY)
    and (shutil.which(_JAILER_BINARY) is not None or Path(_JAILER_BINARY).exists())
    and bool(_STAGE_SCRIPT)
    and Path(_STAGE_SCRIPT).exists()
    and _JAIL_UID > 0
    and _JAIL_GID > 0
)

jailer_skip = pytest.mark.skipif(
    not _JAILER_ENV_AVAILABLE,
    reason=(
        "requires real jailer infra + FABRICA_FC_JAILER/_STAGE_SCRIPT/"
        "_JAIL_UID/_JAIL_GID env vars (Linux only)"
    ),
)


@pytest.fixture
def jailed_backend() -> FirecrackerSandbox:
    return FirecrackerSandbox(
        firecracker_binary=_FC_BINARY,
        kernel_image_path=_FC_KERNEL,
        base_rootfs_path=_FC_ROOTFS,
        use_jailer=True,
        jailer_binary=_JAILER_BINARY,
        jail_uid=_JAIL_UID,
        jail_gid=_JAIL_GID,
        chroot_base_dir=_CHROOT_BASE_DIR,
        stage_script=_STAGE_SCRIPT,
    )


@jailer_skip
async def test_jailer_health_check_true_when_artifacts_present(
    jailed_backend: FirecrackerSandbox,
) -> None:
    assert await jailed_backend.health_check() is True


@jailer_skip
async def test_boot_jailed_produces_a_real_working_tier_2_handle(
    jailed_backend: FirecrackerSandbox,
) -> None:
    handle = await jailed_backend.boot_clean()
    try:
        assert handle.tier == 2
        result = await jailed_backend.execute(
            handle,
            "print('hello from a jailed microVM')",
            on_tool_call=_no_tool_calls,
            timeout=15.0,
        )
        assert result.success is True, result.error_message
        assert result.stdout.strip() == "hello from a jailed microVM"
    finally:
        await jailed_backend.terminate(handle)


@jailer_skip
async def test_jailed_instance_real_tool_call_round_trip(
    jailed_backend: FirecrackerSandbox,
) -> None:
    handle = await jailed_backend.boot_clean()
    try:

        async def add_tool(tool: str, params: dict[str, Any]) -> dict[str, Any]:
            return {"success": True, "value": params["a"] + params["b"], "error_message": None}

        code = (
            "result = namespace.call('add', {'a': 4, 'b': 5})\n"
            "print(f'4 + 5 = {result[\"value\"]}')\n"
        )
        result = await jailed_backend.execute(handle, code, on_tool_call=add_tool, timeout=15.0)
        assert result.success is True
        assert result.stdout.strip() == "4 + 5 = 9"
        assert result.tool_call_count == 1
    finally:
        await jailed_backend.terminate(handle)


@jailer_skip
async def test_terminate_jailed_instance_removes_the_jail_directory(
    jailed_backend: FirecrackerSandbox,
) -> None:
    handle = await jailed_backend.boot_clean()
    state = jailed_backend._instances[handle.id]
    jail_dir = state.jail_dir
    assert jail_dir is not None
    await jailed_backend.terminate(handle)
    # The whole jail directory (kernel + rootfs copy + config + sockets,
    # fc-jail-owned by this point) must be gone -- the scoped
    # fabrica-jailer-cleanup sudoers rule this depends on. Checking
    # non-existence works fine even though this process can't LIST the
    # parent directory (711, traverse-only) -- a nonexistent path's own
    # stat() just returns ENOENT, no permission barrier involved.
    assert not jail_dir.exists()


@jailer_skip
async def test_close_is_a_safe_no_op_for_jailed_backend(
    jailed_backend: FirecrackerSandbox,
) -> None:
    # use_jailer=True allocates no backend-instance-level resource of its
    # own (unlike use_snapshot_restore's golden snapshot) -- every real
    # resource is per-instance-id and already torn down by terminate().
    await jailed_backend.close()  # must not raise
