"""PersistedMemoryStore -- see fabrica/memory/store.py.

Proves the write-through/load-once contract, using a real, minimal
BlobStore double first (isolating this class's own logic), then a real
civitas.plugins.state.InMemoryStateStore + ComponentStateHandle from
fabrica.civitas_bridge, proving duck-typing against the actual class this
was designed to work with -- not just an idealized test double.
"""

from __future__ import annotations

from typing import Any

from fabrica.memory.store import PersistedMemoryStore
from fabrica.memory.types import MemoryItem
from fabrica.scope import Scope


class _FakeBlobStore:
    def __init__(self) -> None:
        self.blob: dict[str, Any] | None = None
        self.set_calls = 0

    async def get(self) -> dict[str, Any] | None:
        return self.blob

    async def set(self, state: dict[str, Any]) -> None:
        self.set_calls += 1
        self.blob = state


class TestCreate:
    async def test_create_with_no_existing_blob_starts_empty(self) -> None:
        store = await PersistedMemoryStore.create(_FakeBlobStore())
        scope = Scope(agent_id="a1")
        assert await store.search(scope, "anything") == []

    async def test_create_rehydrates_from_an_existing_blob(self) -> None:
        blob_store = _FakeBlobStore()
        seed = await PersistedMemoryStore.create(blob_store)
        scope = Scope(agent_id="a1")
        content = "the sandbox pool warms up two instances"
        await seed.write(scope, MemoryItem(id=None, content=content))

        restarted = await PersistedMemoryStore.create(blob_store)
        results = await restarted.search(scope, "sandbox pool")
        assert len(results) == 1
        assert results[0].content == "the sandbox pool warms up two instances"


class TestWriteThrough:
    async def test_write_persists_immediately(self) -> None:
        blob_store = _FakeBlobStore()
        store = await PersistedMemoryStore.create(blob_store)
        await store.write(Scope(agent_id="a1"), MemoryItem(id=None, content="x"))
        assert blob_store.set_calls == 1
        assert blob_store.blob is not None

    async def test_forget_persists_immediately_and_removes_the_item(self) -> None:
        blob_store = _FakeBlobStore()
        store = await PersistedMemoryStore.create(blob_store)
        scope = Scope(agent_id="a1")
        item_id = await store.write(scope, MemoryItem(id=None, content="x"))
        await store.forget(scope, item_id)
        assert blob_store.set_calls == 2
        assert await store.get(scope, item_id) is None

        # Persisted deletion actually removed the record from the blob
        # itself, not just from this instance's in-memory view.
        restarted = await PersistedMemoryStore.create(blob_store)
        assert await restarted.get(scope, item_id) is None

    async def test_search_and_get_never_call_blob_store_set(self) -> None:
        blob_store = _FakeBlobStore()
        store = await PersistedMemoryStore.create(blob_store)
        scope = Scope(agent_id="a1")
        item_id = await store.write(scope, MemoryItem(id=None, content="x"))
        assert blob_store.set_calls == 1
        await store.search(scope, "x")
        await store.get(scope, item_id)
        assert blob_store.set_calls == 1  # unchanged -- reads are never persisted


class TestScopeIsolation:
    async def test_different_scopes_do_not_see_each_others_items(self) -> None:
        blob_store = _FakeBlobStore()
        store = await PersistedMemoryStore.create(blob_store)
        await store.write(Scope(agent_id="a1"), MemoryItem(id=None, content="a1's memory"))
        await store.write(Scope(agent_id="a2"), MemoryItem(id=None, content="a2's memory"))

        restarted = await PersistedMemoryStore.create(blob_store)
        a1_results = await restarted.search(Scope(agent_id="a1"), "memory")
        a2_results = await restarted.search(Scope(agent_id="a2"), "memory")
        assert [r.content for r in a1_results] == ["a1's memory"]
        assert [r.content for r in a2_results] == ["a2's memory"]


class TestRealComponentStateHandle:
    """Duck-typing proof: a real ComponentStateHandle (from
    fabrica.civitas_bridge), never imported by fabrica.memory.store
    itself, satisfies BlobStore without any inheritance relationship.
    """

    async def test_round_trips_through_a_real_civitas_state_store(self) -> None:
        from civitas.plugins.state import InMemoryStateStore

        from fabrica.civitas_bridge.state import _BoundStateHandle

        civitas_store = InMemoryStateStore()
        handle = _BoundStateHandle(civitas_store, "memory_manager")

        store = await PersistedMemoryStore.create(handle)
        scope = Scope(agent_id="a1")
        await store.write(scope, MemoryItem(id=None, content="persisted via real StateStore"))

        # A second PersistedMemoryStore over the SAME real StateStore
        # (simulating a process restart) sees the write.
        restarted = await PersistedMemoryStore.create(
            _BoundStateHandle(civitas_store, "memory_manager")
        )
        results = await restarted.search(scope, "persisted via real")
        assert len(results) == 1
