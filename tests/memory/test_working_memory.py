from __future__ import annotations

import pytest

from fabrica.memory import InMemoryWorkingMemoryStore, WorkingMemoryQuotaExceeded
from fabrica.scope import Scope


@pytest.fixture
def store() -> InMemoryWorkingMemoryStore:
    return InMemoryWorkingMemoryStore()


async def test_remember_then_recall(store: InMemoryWorkingMemoryStore) -> None:
    scope = Scope(session_id="s1")
    await store.remember(scope, "task_progress", {"step": 3})

    assert await store.recall(scope, "task_progress") == {"step": 3}


async def test_recall_unknown_key_returns_none(store: InMemoryWorkingMemoryStore) -> None:
    assert await store.recall(Scope(session_id="s1"), "never_set") is None


async def test_scoped_by_full_tuple_not_just_session_id(store: InMemoryWorkingMemoryStore) -> None:
    """Two agents sharing a session_id must not see each other's working
    memory -- the contract's stated reason for keying by the full Scope
    tuple, not just session_id.
    """
    scope_a = Scope(session_id="shared", agent_id="agent-a")
    scope_b = Scope(session_id="shared", agent_id="agent-b")
    await store.remember(scope_a, "key", "agent-a's value")

    assert await store.recall(scope_b, "key") is None


async def test_snapshot_returns_everything_for_scope(store: InMemoryWorkingMemoryStore) -> None:
    scope = Scope(session_id="s1")
    await store.remember(scope, "a", 1)
    await store.remember(scope, "b", 2)

    snapshot = await store.snapshot(scope)

    assert snapshot == {"a": 1, "b": 2}


async def test_clear_wipes_scope(store: InMemoryWorkingMemoryStore) -> None:
    scope = Scope(session_id="s1")
    await store.remember(scope, "a", 1)

    await store.clear(scope)

    assert await store.snapshot(scope) == {}


async def test_clear_on_empty_scope_is_a_noop(store: InMemoryWorkingMemoryStore) -> None:
    await store.clear(Scope(session_id="never-used"))  # must not raise


async def test_remember_raises_when_quota_exceeded() -> None:
    store = InMemoryWorkingMemoryStore(quota_bytes=100)
    scope = Scope(session_id="s1")

    with pytest.raises(WorkingMemoryQuotaExceeded):
        await store.remember(scope, "big", "x" * 1000)
