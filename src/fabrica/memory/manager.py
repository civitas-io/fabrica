"""MemoryManager -- the facade. See docs/contracts/memory.md.

Adds no logic of its own beyond construction-time composition -- every
method is a direct pass-through to one of the three injected backends.
"""

from __future__ import annotations

from typing import Any

from fabrica.memory.compactor import Compactor
from fabrica.memory.store import MemoryStore
from fabrica.memory.types import CompactionResult, MemoryItem, Message
from fabrica.memory.working_memory import WorkingMemoryStore
from fabrica.observability import NullTracer, Tracer, traced
from fabrica.scope import Scope


class MemoryManager:
    def __init__(
        self,
        working: WorkingMemoryStore,
        long_term: MemoryStore,
        compactor: Compactor,
        *,
        tracer: Tracer | None = None,
    ) -> None:
        self._working = working
        self._long_term = long_term
        self._compactor = compactor
        # `tracer` emits `fabrica.memory.write`/`fabrica.memory.search`
        # (system-design.md §7) -- defaults to NullTracer(), a real no-op,
        # matching the NullPresidiumClient/NullCompactor DI pattern.
        self._tracer = tracer if tracer is not None else NullTracer()

    # working memory
    async def remember(self, scope: Scope, key: str, value: Any) -> None:
        await self._working.remember(scope, key, value)

    async def recall(self, scope: Scope, key: str) -> Any | None:
        return await self._working.recall(scope, key)

    # compaction -- MemoryManager stays ignorant of summarization mechanics
    async def compact(self, messages: list[Message], *, budget_tokens: int) -> CompactionResult:
        return await self._compactor.compact(messages, budget_tokens=budget_tokens)

    # long-term memory
    async def write(self, scope: Scope, item: MemoryItem) -> str:
        with traced(
            self._tracer,
            "fabrica.memory.write",
            backend=type(self._long_term).__name__,
            user_id=scope.user_id,
            session_id=scope.session_id,
            agent_id=scope.agent_id,
            team_id=scope.team_id,
        ) as span:
            memory_id = await self._long_term.write(scope, item)
            span.set_attribute("memory_id", memory_id)
            # Real usage/budget consumption dimension
            # (civitas-presidium-integration.md's "MemoryStore ... read/write
            # volume") -- content bytes, not just an item count, since a
            # ledger rolling up storage consumption needs the actual size.
            span.set_attribute("volume_bytes", len(item.content.encode()))
            return memory_id

    async def search(self, scope: Scope, query: str, limit: int = 5) -> list[MemoryItem]:
        with traced(
            self._tracer,
            "fabrica.memory.search",
            backend=type(self._long_term).__name__,
            query=query,
            limit=limit,
            user_id=scope.user_id,
            session_id=scope.session_id,
            agent_id=scope.agent_id,
            team_id=scope.team_id,
        ) as span:
            results = await self._long_term.search(scope, query, limit)
            span.set_attribute("result_count", len(results))
            span.set_attribute("volume_bytes", sum(len(item.content.encode()) for item in results))
            return results

    async def get(self, scope: Scope, id: str) -> MemoryItem | None:
        return await self._long_term.get(scope, id)

    async def forget(self, scope: Scope, id: str) -> None:
        await self._long_term.forget(scope, id)
