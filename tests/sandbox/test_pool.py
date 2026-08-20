"""Tests for SandboxPool -- proving the warm-pool/bounded-overflow contract,
not just that acquire()/release() run without error.
"""

from __future__ import annotations

import asyncio
from typing import Any

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
        self.closed = False
        self._tier = tier
        self.last_tool_call_timeout: float | None = None

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
        tool_call_timeout: float | None = None,
    ) -> RunResult:
        self.last_tool_call_timeout = tool_call_timeout
        raise NotImplementedError("not exercised by these tests")

    async def terminate(self, handle: SandboxHandle) -> None:
        self.terminated.append(handle.id)

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


async def _unused(tool: str, params: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError("not exercised by these tests")


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


async def test_close_terminates_every_warm_instance() -> None:
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=3, max_concurrent=5)
    await pool.prewarm()

    await pool.close()

    assert sorted(backend.terminated) == ["h1", "h2", "h3"]


async def test_close_is_a_no_op_on_an_already_closed_pool() -> None:
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=2, max_concurrent=5)
    await pool.prewarm()

    await pool.close()
    terminated_after_first_close = list(backend.terminated)
    await pool.close()  # must not raise, must not double-terminate anything

    assert backend.terminated == terminated_after_first_close


async def test_close_waits_for_an_in_flight_refill_before_draining() -> None:
    """The real gap this closes: a release()-triggered background refill
    that completes AFTER close() has already drained the warm list used
    to leak one more never-terminated instance (found by inspecting the
    real filesystem after real FirecrackerSandbox test runs -- this test
    proves the fix against the fast fake backend, deterministically).
    """
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=1, max_concurrent=5)
    handle = await pool.acquire()  # warm pool empty, 1 concurrent slot used

    await pool.release(handle)  # triggers a background refill -- not yet awaited
    await pool.close()  # must wait for that refill, then terminate its result

    # The released handle AND the instance the refill produced are both
    # terminated -- nothing left dangling because close() raced ahead of
    # the in-flight boot_clean().
    assert sorted(backend.terminated) == ["h1", "h2"]


async def test_close_also_closes_the_backend() -> None:
    # The real gap this closes: SandboxPool.close() used to only call
    # terminate() per warm handle -- nothing ever released a backend's
    # own instance-level resources (found against a real SrtSandbox,
    # which allocates a directory in __init__ that terminate() never
    # touches). backend.close() must run, and only AFTER every handle is
    # already terminated.
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=2, max_concurrent=5)
    await pool.prewarm()

    await pool.close()

    assert backend.closed is True
    assert sorted(backend.terminated) == ["h1", "h2"]  # already drained before close() ran


async def test_warm_count_reflects_prewarm_and_close() -> None:
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=2, max_concurrent=5)
    assert pool.warm_count == 0

    await pool.prewarm()
    assert pool.warm_count == 2

    await pool.close()
    assert pool.warm_count == 0


async def test_run_passes_tool_call_timeout_through_to_the_backend() -> None:
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=0, max_concurrent=5)
    handle = await pool.acquire()

    with pytest.raises(NotImplementedError):
        await pool.run(handle, "code", on_tool_call=_unused, tool_call_timeout=1.5)

    assert backend.last_tool_call_timeout == 1.5


async def test_run_defaults_tool_call_timeout_to_none() -> None:
    backend = _FakeBackend()
    pool = SandboxPool(backend, warm_size=0, max_concurrent=5)
    handle = await pool.acquire()

    with pytest.raises(NotImplementedError):
        await pool.run(handle, "code", on_tool_call=_unused)

    assert backend.last_tool_call_timeout is None
