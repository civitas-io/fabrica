from __future__ import annotations

import asyncio

from fabrica.prompts import InMemoryPromptStore


async def test_put_creates_version_1_for_new_name() -> None:
    store = InMemoryPromptStore()
    template = await store.put("greeting", "Hello!")
    assert template.version == 1
    assert template.name == "greeting"


async def test_put_never_overwrites_always_creates_new_version() -> None:
    store = InMemoryPromptStore()
    await store.put("greeting", "v1 content")
    second = await store.put("greeting", "v2 content")

    assert second.version == 2
    first = await store.get("greeting", 1)
    assert first is not None
    assert first.content == "v1 content"  # unchanged


async def test_get_none_returns_latest() -> None:
    store = InMemoryPromptStore()
    await store.put("greeting", "v1")
    await store.put("greeting", "v2")

    latest = await store.get("greeting")

    assert latest is not None
    assert latest.version == 2
    assert latest.content == "v2"


async def test_get_unknown_name_returns_none() -> None:
    store = InMemoryPromptStore()
    assert await store.get("never-created") is None


async def test_get_unknown_version_of_known_name_returns_none() -> None:
    store = InMemoryPromptStore()
    await store.put("greeting", "v1")
    assert await store.get("greeting", 99) is None


async def test_list_versions_ascending() -> None:
    store = InMemoryPromptStore()
    await store.put("greeting", "v1")
    await store.put("greeting", "v2")
    await store.put("greeting", "v3")

    assert await store.list_versions("greeting") == [1, 2, 3]


async def test_list_versions_empty_for_unknown_name() -> None:
    store = InMemoryPromptStore()
    assert await store.list_versions("never-created") == []


async def test_list_names_returns_all_registered_names() -> None:
    store = InMemoryPromptStore()
    await store.put("greeting", "x")
    await store.put("farewell", "y")

    assert set(await store.list_names()) == {"greeting", "farewell"}


async def test_delete_specific_version() -> None:
    store = InMemoryPromptStore()
    await store.put("greeting", "v1")
    await store.put("greeting", "v2")

    await store.delete("greeting", 1)

    assert await store.get("greeting", 1) is None
    assert await store.get("greeting", 2) is not None


async def test_delete_all_versions_when_version_is_none() -> None:
    store = InMemoryPromptStore()
    await store.put("greeting", "v1")
    await store.put("greeting", "v2")

    await store.delete("greeting")

    assert await store.list_versions("greeting") == []


async def test_delete_unknown_is_a_noop() -> None:
    store = InMemoryPromptStore()
    await store.delete("never-created")  # must not raise


async def test_cacheable_and_cache_boundary_stored_verbatim() -> None:
    store = InMemoryPromptStore()
    template = await store.put("system-prompt", "static content", cacheable=True, cache_boundary=42)

    assert template.cacheable is True
    assert template.cache_boundary == 42


async def test_concurrent_puts_assign_distinct_versions() -> None:
    """Open item 2: version assignment must be atomic under concurrency,
    not a race that could produce duplicate version numbers.
    """
    store = InMemoryPromptStore()
    results = await asyncio.gather(*[store.put("greeting", f"v{i}") for i in range(20)])

    versions = sorted(t.version for t in results)
    assert versions == list(range(1, 21))  # no duplicates, no gaps
