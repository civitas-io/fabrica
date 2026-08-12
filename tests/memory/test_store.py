from __future__ import annotations

from fabrica.memory import InMemoryMemoryStore, MemoryItem
from fabrica.scope import Scope


async def test_write_assigns_id_and_get_retrieves_it() -> None:
    store = InMemoryMemoryStore()
    scope = Scope(user_id="u1")

    new_id = await store.write(scope, MemoryItem(id=None, content="the user prefers dark mode"))
    retrieved = await store.get(scope, new_id)

    assert retrieved is not None
    assert retrieved.content == "the user prefers dark mode"
    assert retrieved.id == new_id


async def test_get_unknown_id_returns_none_not_error() -> None:
    store = InMemoryMemoryStore()
    assert await store.get(Scope(user_id="u1"), "never-written") is None


async def test_search_returns_empty_list_for_empty_scope() -> None:
    store = InMemoryMemoryStore()
    results = await store.search(Scope(user_id="u1"), "anything")
    assert results == []


async def test_search_ranks_by_relevance_and_populates_score() -> None:
    store = InMemoryMemoryStore()
    scope = Scope(user_id="u1")
    await store.write(scope, MemoryItem(id=None, content="the user's budget ceiling is $2400"))
    await store.write(scope, MemoryItem(id=None, content="the user likes Italian food"))

    results = await store.search(scope, "budget")

    assert results[0].content == "the user's budget ceiling is $2400"
    assert results[0].score is not None  # deliberately kept, unlike RankedMatch


async def test_scoped_by_full_tuple() -> None:
    store = InMemoryMemoryStore()
    await store.write(Scope(user_id="u1"), MemoryItem(id=None, content="u1's memory"))

    results = await store.search(Scope(user_id="u2"), "memory")

    assert results == []


async def test_forget_removes_item() -> None:
    store = InMemoryMemoryStore()
    scope = Scope(user_id="u1")
    item_id = await store.write(scope, MemoryItem(id=None, content="temporary note"))

    await store.forget(scope, item_id)

    assert await store.get(scope, item_id) is None


async def test_forget_unknown_id_is_a_noop() -> None:
    store = InMemoryMemoryStore()
    await store.forget(Scope(user_id="u1"), "never-existed")  # must not raise
