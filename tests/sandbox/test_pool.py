"""Tests for SandboxPool -- proving the warm-pool/bounded-overflow contract,
not just that acquire()/release() run without error.
"""

from __future__ import annotations

import asyncio

import pytest

from fabrica.sandbox import (
    RunResult,
    SandboxHandle,
    SandboxPool,
    SandboxPoolExhaustedError,
    ToolCallCallback,
)


class _FakeBackend:
    """A minimal, fast Sandbox backend for testing SandboxPool's own
    bookkeeping in isolation from real subprocess/ZMQ cost.
    """

    def __init__(self, tier: int = 0) -> None:
        self.boot_count = 0
        self.terminated: list[str] = []
        self._tier = tier

    @property
    def tier(self) -> int:
        return self._tier

    async def boot_clean(self) -> SandboxHandle:
        self.boot_count += 1
        return SandboxHandle(id=f"h{self.boot_count}", tier=0)

    async def execute(
        self,
        handle: SandboxHandle,
        code: str,
        *,
        on_tool_call: ToolCallCallback,
        timeout: float,
    ) -> RunResult:
        raise NotImplementedError("not exercised by these tests")

    async def terminate(self, handle: SandboxHandle) -> None:
        self.terminated.append(handle.id)

    async def health_check(self) -> bool:
        return True


def test_tier_property_delegates_to_backend() -> None:
    pool = SandboxPool(_FakeBackend(tier=2), warm_size=1, max_concurrent=1)
    assert pool.tier == 2


async def test_prewarm_fills_warm_pool() -> None:
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=3, max_concurrent=5)

    await pool.prewarm()

    assert backend.boot_count == 3


async def test_acquire_prefers_warm_pool_over_cold_start() -> None:
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=2, max_concurrent=5)
    await pool.prewarm()
    assert backend.boot_count == 2

    await pool.acquire()

    # Took from the warm pool -- no additional boot_clean() call.
    assert backend.boot_count == 2


async def test_acquire_cold_starts_when_warm_pool_empty_and_under_max() -> None:
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=0, max_concurrent=5)

    handle = await pool.acquire()

    assert handle.id == "h1"
    assert backend.boot_count == 1


async def test_acquire_raises_exhausted_when_at_max_concurrent() -> None:
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=0, max_concurrent=1, acquire_timeout=0.2)
    await pool.acquire()  # fills the one concurrent slot

    with pytest.raises(SandboxPoolExhaustedError):
        await pool.acquire()


async def test_release_always_terminates_never_reuses() -> None:
    """The corrected release() semantics from contracts/sandbox.md: the
    USED instance is always terminated, never handed back out live --
    even though the pool "regrows" back toward warm_size afterward.
    """
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=1, max_concurrent=5)
    handle = await pool.acquire()

    await pool.release(handle)

    assert handle.id in backend.terminated
    # Give the background refill task a chance to run.
    await asyncio.sleep(0.05)
    # A FRESH instance was booted to refill the warm slot -- not the
    # released one reused live.
    assert backend.boot_count == 2  # 1 original cold-start + 1 refill


async def test_release_frees_a_slot_for_a_waiting_acquire() -> None:
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=0, max_concurrent=1, acquire_timeout=2.0)
    handle = await pool.acquire()

    async def _release_soon() -> None:
        await asyncio.sleep(0.1)
        await pool.release(handle)

    release_task = asyncio.ensure_future(_release_soon())
    second_handle = await pool.acquire()  # must NOT raise -- waits, then succeeds

    assert second_handle is not None
    await release_task


async def test_release_does_not_exceed_warm_size_on_refill() -> None:
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=1, max_concurrent=5)
    await pool.prewarm()  # warm pool already has 1
    handle = await pool.acquire()  # takes the warm one, warm pool now empty

    await pool.release(handle)
    await asyncio.sleep(0.05)

    # Exactly one refill happened, not more -- warm_size=1 respected.
    assert backend.boot_count == 2  # 1 prewarm + 1 refill
