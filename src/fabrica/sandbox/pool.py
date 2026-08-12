"""SandboxPool -- the public engine. See docs/contracts/sandbox.md.

ToolManager and SkillManager depend on this, never on a Sandbox backend
directly.
"""

from __future__ import annotations

import asyncio
import logging

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
    ) -> None:
        """`backend` is resolved by CivitasBridge at construction time
        based on host OS + deployment tier -- never chosen per-call.
        `warm_size` and `max_concurrent` implement the bounded-overflow
        design from system-design.md §6/§7.
        """
        self._backend = backend
        self._warm_size = warm_size
        self._max_concurrent = max_concurrent
        self._acquire_timeout = acquire_timeout

        self._warm: list[SandboxHandle] = []
        self._concurrent_count = 0
        self._condition = asyncio.Condition()

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

    async def acquire(self) -> SandboxHandle:
        """Tries the warm pool first. If empty and under max_concurrent,
        cold-starts on demand. If at max_concurrent, queues up to
        acquire_timeout.

        Raises:
            SandboxPoolExhaustedError: no handle became available in time.
        """
        try:
            async with asyncio.timeout(self._acquire_timeout):
                async with self._condition:
                    while True:
                        if self._warm:
                            handle = self._warm.pop()
                            self._concurrent_count += 1
                            return handle
                        if self._concurrent_count < self._max_concurrent:
                            self._concurrent_count += 1
                            # Cold-start outside the lock body conceptually,
                            # but boot_clean() is awaited while still holding
                            # the condition -- acceptable here since
                            # SandboxPool's own bookkeeping (not the boot
                            # itself) is what needs the lock; a future
                            # optimization could release the lock around
                            # the actual boot_clean() call if it becomes a
                            # real contention bottleneck.
                            return await self._backend.boot_clean()
                        # At max_concurrent -- wait for a release() to free
                        # a slot or refill the warm pool.
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
    ) -> RunResult:
        """Delegates directly to the backend. Raises SandboxTimeoutError or
        SandboxCrashedError unchanged -- the handle is not usable after
        either, per the contract.
        """
        return await self._backend.execute(
            handle, code, on_tool_call=on_tool_call, timeout=timeout
        )

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
            # cold-start cost.
            asyncio.ensure_future(self._refill_one())

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
