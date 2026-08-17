"""PersistedPromptStore -- see fabrica/prompts/store.py.

Same discipline as tests/memory/test_persisted_store.py: a minimal
BlobStore double first, then a real civitas.plugins.state.InMemoryStateStore
+ ComponentStateHandle proving real duck-typing.
"""

from __future__ import annotations

from typing import Any

from fabrica.prompts.store import PersistedPromptStore


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
        store = await PersistedPromptStore.create(_FakeBlobStore())
        assert await store.list_names() == []

    async def test_create_rehydrates_from_an_existing_blob(self) -> None:
        blob_store = _FakeBlobStore()
        seed = await PersistedPromptStore.create(blob_store)
        # "Hello, {{name}}!" is 16 characters -- 7 (the real, static
        # "Hello, " prefix) is a valid boundary, not the old test's
        # out-of-range 42 (kept passing verbatim before contracts/
        # prompts.md open item 4's construction-time validation existed
        # to reject it).
        await seed.put("greeting", "Hello, {{name}}!", cacheable=True, cache_boundary=7)

        restarted = await PersistedPromptStore.create(blob_store)
        template = await restarted.get("greeting")
        assert template is not None
        assert template.content == "Hello, {{name}}!"
        assert template.cacheable is True
        assert template.cache_boundary == 7
        assert template.version == 1


class TestWriteThrough:
    async def test_put_persists_immediately(self) -> None:
        blob_store = _FakeBlobStore()
        store = await PersistedPromptStore.create(blob_store)
        await store.put("p", "content")
        assert blob_store.set_calls == 1

    async def test_multiple_versions_all_survive_a_restart(self) -> None:
        blob_store = _FakeBlobStore()
        store = await PersistedPromptStore.create(blob_store)
        await store.put("p", "v1")
        await store.put("p", "v2")

        restarted = await PersistedPromptStore.create(blob_store)
        assert await restarted.list_versions("p") == [1, 2]
        v1 = await restarted.get("p", 1)
        assert v1 is not None
        assert v1.content == "v1"
        latest = await restarted.get("p")  # version=None resolves to latest
        assert latest is not None
        assert latest.content == "v2"

    async def test_delete_specific_version_persists_immediately(self) -> None:
        blob_store = _FakeBlobStore()
        store = await PersistedPromptStore.create(blob_store)
        await store.put("p", "v1")
        await store.put("p", "v2")
        await store.delete("p", 1)

        restarted = await PersistedPromptStore.create(blob_store)
        assert await restarted.list_versions("p") == [2]

    async def test_get_and_list_never_call_blob_store_set(self) -> None:
        blob_store = _FakeBlobStore()
        store = await PersistedPromptStore.create(blob_store)
        await store.put("p", "v1")
        assert blob_store.set_calls == 1
        await store.get("p")
        await store.list_versions("p")
        await store.list_names()
        assert blob_store.set_calls == 1


class TestRealComponentStateHandle:
    async def test_round_trips_through_a_real_civitas_state_store(self) -> None:
        from civitas.plugins.state import InMemoryStateStore

        from fabrica.civitas_bridge.state import _BoundStateHandle

        civitas_store = InMemoryStateStore()
        handle = _BoundStateHandle(civitas_store, "prompts_manager")

        store = await PersistedPromptStore.create(handle)
        await store.put("greeting", "Hello!", metadata={"author": "ops"})

        restarted = await PersistedPromptStore.create(
            _BoundStateHandle(civitas_store, "prompts_manager")
        )
        template = await restarted.get("greeting")
        assert template is not None
        assert template.content == "Hello!"
        assert template.metadata == {"author": "ops"}
