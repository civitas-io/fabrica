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


async def test_boot_clean_raises_crashed_error_for_a_bad_kernel_path() -> None:
    backend = FirecrackerSandbox(
        firecracker_binary=_FC_BINARY,
        kernel_image_path="/no/such/kernel",
        base_rootfs_path=_FC_ROOTFS,
    )
    with pytest.raises(SandboxCrashedError):
        await backend.boot_clean()
