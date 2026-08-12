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
from fabrica.scope import Scope


class MemoryManager:
    def __init__(
        self,
        working: WorkingMemoryStore,
        long_term: MemoryStore,
        compactor: Compactor,
    ) -> None:
        self._working = working
        self._long_term = long_term
        self._compactor = compactor

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
        return await self._long_term.write(scope, item)

    async def search(self, scope: Scope, query: str, limit: int = 5) -> list[MemoryItem]:
        return await self._long_term.search(scope, query, limit)

    async def get(self, scope: Scope, id: str) -> MemoryItem | None:
        return await self._long_term.get(scope, id)

    async def forget(self, scope: Scope, id: str) -> None:
        await self._long_term.forget(scope, id)
