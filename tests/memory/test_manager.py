"""MemoryManager -- proving thin delegation to all three facets, composed
together, not each facet in isolation.
"""

from __future__ import annotations

from typing import Any

from fabrica.memory import (
    InMemoryMemoryStore,
    InMemoryWorkingMemoryStore,
    MemoryItem,
    MemoryManager,
    Message,
    NullCompactor,
)
from fabrica.scope import Scope


async def test_manager_delegates_working_memory() -> None:
    manager = MemoryManager(
        InMemoryWorkingMemoryStore(), InMemoryMemoryStore(), NullCompactor()
    )
    scope = Scope(session_id="s1")

    await manager.remember(scope, "key", "value")

    assert await manager.recall(scope, "key") == "value"


async def test_manager_delegates_long_term_memory() -> None:
    manager = MemoryManager(
        InMemoryWorkingMemoryStore(), InMemoryMemoryStore(), NullCompactor()
    )
    scope = Scope(user_id="u1")

    item_id = await manager.write(scope, MemoryItem(id=None, content="a fact worth keeping"))
    results = await manager.search(scope, "fact")

    assert results[0].id == item_id
    await manager.forget(scope, item_id)
    assert await manager.get(scope, item_id) is None


async def test_manager_delegates_compaction() -> None:
    class _FakeSummarizer:
        async def summarize(self, messages: list[Any], *, target_tokens: int) -> str:
            return "summarized"

    from fabrica.memory import RecencyCompactor

    manager = MemoryManager(
        InMemoryWorkingMemoryStore(),
        InMemoryMemoryStore(),
        RecencyCompactor(_FakeSummarizer(), preserve_last_n=1),
    )
    messages = [
        Message(role="user", content="old", tokens=50),
        Message(role="user", content="new", tokens=10),
    ]

    result = await manager.compact(messages, budget_tokens=20)

    assert result.summary == "summarized"
