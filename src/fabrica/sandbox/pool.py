"""SandboxPool -- the public engine. See docs/contracts/sandbox.md.

ToolManager and SkillManager depend on this, never on a Sandbox backend
directly.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fabrica.observability import NullTracer, Tracer, traced
from fabrica.sandbox.backend import Sandbox
from fabrica.sandbox.errors import SandboxPoolExhaustedError
from fabrica.sandbox.types import RunResult, SandboxHandle, ToolCallCallback

logger = logging.getLogger(__name__)


class SandboxPool:
    def __init__(
        self,
        backend: Sandbox,
        *,
        warm_size: int,
        max_concurrent: int,
        acquire_timeout: float = 5.0,
        tracer: Tracer | None = None,
    ) -> None:
        """`backend` is resolved by CivitasBridge at construction time
        based on host OS + deployment tier -- never chosen per-call.
        `warm_size` and `max_concurrent` implement the bounded-overflow
        design from system-design.md §6/§7.

        `tracer` emits `fabrica.sandbox.acquire`/`fabrica.sandbox.run`
        (system-design.md §7) -- defaults to NullTracer(), a real no-op,
        matching the NullPresidiumClient/NullCompactor DI pattern.
        """
        self._backend = backend
        self._warm_size = warm_size
        self._max_concurrent = max_concurrent
        self._acquire_timeout = acquire_timeout
        self._tracer = tracer if tracer is not None else NullTracer()

        self._warm: list[SandboxHandle] = []
        self._concurrent_count = 0
        self._condition = asyncio.Condition()
        self._refill_tasks: set[asyncio.Task[None]] = set()

    @property
    def tier(self) -> int:
        """Delegates to the wrapped backend -- SandboxPool never chooses
        or changes tier itself, only CivitasBridge's platform dispatch
        does (isolation.md), once, at construction.
        """
        return self._backend.tier

    @property
    def warm_count(self) -> int:
        """How many instances are currently sitting in the warm pool --
        exposed publicly so callers (and tests) can observe close()'s
        effect without reaching into private state.
        """
        return len(self._warm)

    async def prewarm(self) -> None:
        """Not part of the contract's method list, but needed to actually
        populate the warm pool at startup -- otherwise every deployment
        would start with an empty warm pool and pay a cold-start on its
        very first acquire() regardless of warm_size. CivitasBridge's
        build() is expected to call this once, per warm_size.
        """
        async with self._condition:
            while len(self._warm) < self._warm_size:
                handle = await self._backend.boot_clean()
                self._warm.append(handle)

    async def acquire(
        self, *, trace_id: str = "", parent_span_id: str | None = None
    ) -> SandboxHandle:
        """Tries the warm pool first. If empty and under max_concurrent,
        cold-starts on demand. If at max_concurrent, queues up to
        acquire_timeout.

        `trace_id`/`parent_span_id` let a caller (e.g. `execute_in_sandbox`)
        nest this span under its own -- both default to "start a fresh
        root span".

        Raises:
            SandboxPoolExhaustedError: no handle became available in time.
        """
        start = time.monotonic()
        with traced(
            self._tracer,
            "fabrica.sandbox.acquire",
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            tier=self.tier,
        ) as span:
            try:
                async with asyncio.timeout(self._acquire_timeout):
                    async with self._condition:
                        while True:
                            if self._warm:
                                handle = self._warm.pop()
                                self._concurrent_count += 1
                                span.set_attribute("warm_hit", True)
                                span.set_attribute(
                                    "wait_ms", round((time.monotonic() - start) * 1000, 2)
                                )
                                return handle
                            if self._concurrent_count < self._max_concurrent:
                                self._concurrent_count += 1
                                # Cold-start outside the lock body
                                # conceptually, but boot_clean() is awaited
                                # while still holding the condition --
                                # acceptable here since SandboxPool's own
                                # bookkeeping (not the boot itself) is what
                                # needs the lock; a future optimization
                                # could release the lock around the actual
                                # boot_clean() call if it becomes a real
                                # contention bottleneck.
                                handle = await self._backend.boot_clean()
                                span.set_attribute("warm_hit", False)
                                span.set_attribute(
                                    "wait_ms", round((time.monotonic() - start) * 1000, 2)
                                )
                                return handle
                            # At max_concurrent -- wait for a release() to
                            # free a slot or refill the warm pool.
                            await self._condition.wait()
            except TimeoutError:
                raise SandboxPoolExhaustedError(
                    f"no sandbox handle available within {self._acquire_timeout}s "
                    f"(warm={len(self._warm)}, concurrent={self._concurrent_count}, "
                    f"max_concurrent={self._max_concurrent})"
                ) from None

    async def run(
        self,
        handle: SandboxHandle,
        code: str,
        *,
        on_tool_call: ToolCallCallback,
        timeout: float = 30.0,
        trace_id: str = "",
        parent_span_id: str | None = None,
    ) -> RunResult:
        """Delegates directly to the backend. Raises SandboxTimeoutError or
        SandboxCrashedError unchanged -- the handle is not usable after
        either, per the contract. When either is raised, the span records
        it via `set_error()` (real error attribution, not silence) before
        propagating unchanged -- `traced()`'s own contract.

        `trace_id`/`parent_span_id` let a caller nest this span under its
        own, same as `acquire()`.
        """
        with traced(
            self._tracer,
            "fabrica.sandbox.run",
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            tier=self.tier,
        ) as span:
            result = await self._backend.execute(
                handle, code, on_tool_call=on_tool_call, timeout=timeout
            )
            span.set_attribute("duration_ms", result.duration_ms)
            span.set_attribute("cpu_seconds", result.cpu_seconds)
            span.set_attribute("exit_status", "ok" if result.success else "error")
            return result

    async def release(self, handle: SandboxHandle) -> None:
        """The underlying instance is ALWAYS terminated -- never reused
        live. If the pool is under warm_size after this, triggers a
        background boot_clean() to restore a FRESH instance, not a reuse
        of the just-released one (the correction this contract makes to
        system-design.md's original "regrow the pool" language).
        """
        await self._backend.terminate(handle)

        async with self._condition:
            self._concurrent_count -= 1
            needs_refill = len(self._warm) < self._warm_size
            self._condition.notify_all()

        if needs_refill:
            # Fire-and-forget, per contracts/sandbox.md's open item 1
            # ("background-replenishment trigger... not decided here") --
            # this is a concrete choice for that open item, not a silent
            # default: refill happens asynchronously, not blocking
            # release() itself, so a caller isn't penalized by the next
            # cold-start cost. Tracked in _refill_tasks (not truly
            # fire-and-forget-forgotten) so close() can wait for it --
            # a real gap found by testing this pool wrapped around a real
            # backend (FirecrackerSandbox) instead of only the in-memory
            # _FakeBackend: an in-flight refill that outlives shutdown
            # used to leak one more never-terminated instance.
            task = asyncio.ensure_future(self._refill_one())
            self._refill_tasks.add(task)
            task.add_done_callback(self._refill_tasks.discard)

    async def _refill_one(self) -> None:
        try:
            fresh = await self._backend.boot_clean()
        except Exception:
            logger.warning("SandboxPool: background refill boot_clean() failed", exc_info=True)
            return
        async with self._condition:
            if len(self._warm) < self._warm_size:
                self._warm.append(fresh)
                self._condition.notify_all()
            else:
                # Pool got refilled by another path already (or warm_size
                # shrank) -- don't exceed warm_size, terminate the extra.
                await self._backend.terminate(fresh)

    async def close(self) -> None:
        """Terminate every instance still resident in the warm pool.
        Must be called once at deployment shutdown -- see
        docs/contracts/sandbox.md for the real gap this closes (found by
        inspecting the filesystem after real FirecrackerSandbox test
        runs: warm-pool instances were never terminated at all).

        Waits for any in-flight background refill task FIRST, so a
        refill that completes mid-close doesn't add one more instance
        to the warm list after it's already been drained.
        """
        pending = list(self._refill_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        async with self._condition:
            handles, self._warm = self._warm, []

        for handle in handles:
            await self._backend.terminate(handle)
