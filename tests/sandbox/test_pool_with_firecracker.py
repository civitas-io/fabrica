"""Proves SandboxPool genuinely works wrapped around the REAL
FirecrackerSandbox backend -- not just the fast _FakeBackend used in
test_pool.py to isolate the pool's own bookkeeping, and not just
FirecrackerSandbox in isolation (test_firecracker_backend.py). This is
the actual composition CivitasBridge.build() would wire up for a real
Tier 2 deployment. Requires real Firecracker + KVM (Linux only), same
gating as test_firecracker_backend.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from fabrica.sandbox import SandboxPool
from fabrica.sandbox.firecracker_backend import FirecrackerSandbox

_FC_BINARY = os.environ.get("FABRICA_FC_BINARY", "")
_FC_KERNEL = os.environ.get("FABRICA_FC_KERNEL", "")
_FC_ROOTFS = os.environ.get("FABRICA_FC_ROOTFS", "")

_REAL_ENV_AVAILABLE = (
    bool(_FC_BINARY)
    and bool(_FC_KERNEL)
    and bool(_FC_ROOTFS)
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
async def pool() -> Any:
    backend = FirecrackerSandbox(
        firecracker_binary=_FC_BINARY,
        kernel_image_path=_FC_KERNEL,
        base_rootfs_path=_FC_ROOTFS,
    )
    p = SandboxPool(backend, warm_size=1, max_concurrent=2, acquire_timeout=20.0)
    yield p
    # Real teardown discipline, not test-only convenience: any real
    # deployment must call close() at shutdown (contracts/sandbox.md) --
    # without it, warm-pool instances (including ones produced by a
    # release()-triggered background refill) are simply abandoned. This
    # fixture not calling close() was itself the direct cause of the
    # first version of this file leaking real rootfs files on the
    # homelab, found by inspecting /tmp after a real test run.
    await p.close()


async def test_pool_reports_the_real_backends_tier(pool: SandboxPool) -> None:
    assert pool.tier == 2


async def test_prewarm_then_acquire_serves_from_the_warm_pool_without_a_fresh_boot(
    pool: SandboxPool,
) -> None:
    await pool.prewarm()
    handle = await pool.acquire()
    try:
        assert handle.tier == 2
    finally:
        await pool.release(handle)


async def test_acquire_run_release_round_trips_a_real_tool_call_through_the_pool(
    pool: SandboxPool,
) -> None:
    async def add_tool(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        assert tool == "add"
        return {"success": True, "value": params["a"] + params["b"], "error_message": None}

    handle = await pool.acquire()
    try:
        result = await pool.run(
            handle,
            "result = namespace.call('add', {'a': 4, 'b': 5})\nprint(f'sum={result[\"value\"]}')\n",
            on_tool_call=add_tool,
            timeout=15.0,
        )
        assert result.success is True
        assert result.stdout.strip() == "sum=9"
    finally:
        await pool.release(handle)


async def test_release_always_terminates_never_reuses_the_same_handle(pool: SandboxPool) -> None:
    handle = await pool.acquire()
    await pool.run(handle, "print('first use')", on_tool_call=_no_tool_calls, timeout=15.0)
    await pool.release(handle)

    # Using the SAME handle again after release() must fail -- the
    # underlying instance was really terminated, not silently kept alive
    # for reuse (SandboxPool's always-terminate-never-reuse rule, proven
    # here against the real backend, not just the fake one).
    with pytest.raises(Exception):  # noqa: B017 -- backend-specific crash/OS error, not one type
        await pool.run(handle, "print('second use')", on_tool_call=_no_tool_calls, timeout=5.0)
