from __future__ import annotations

from pathlib import Path

import pytest

from fabrica.prompts import InMemoryPromptStore, PromptManager, PromptParseError


@pytest.fixture
def manager() -> PromptManager:
    return PromptManager(InMemoryPromptStore())


async def test_put_then_get(manager: PromptManager) -> None:
    await manager.put("greeting", "Hello!")
    template = await manager.get("greeting")
    assert template is not None
    assert template.content == "Hello!"


async def test_get_is_cached_avoids_backend_round_trip(manager: PromptManager) -> None:
    await manager.put("greeting", "Hello!")
    first = await manager.get("greeting", 1)
    second = await manager.get("greeting", 1)
    assert first is second  # same cached object, not just equal


async def test_put_invalidates_latest_cache_entry(manager: PromptManager) -> None:
    await manager.put("greeting", "v1")
    await manager.get("greeting")  # populate the version=None cache entry
    await manager.put("greeting", "v2")

    latest = await manager.get("greeting")

    assert latest is not None
    assert latest.content == "v2"  # not the stale cached v1


async def test_delete_invalidates_all_cached_entries_for_name(manager: PromptManager) -> None:
    await manager.put("greeting", "v1")
    await manager.put("greeting", "v2")
    await manager.get("greeting", 1)
    await manager.get("greeting")  # caches version=None -> v2

    await manager.delete("greeting", 2)

    # version=None must now resolve to v1, not the stale cached v2.
    latest = await manager.get("greeting")
    assert latest is not None
    assert latest.version == 1


async def test_load_reads_prompt_md_and_puts_it(manager: PromptManager, tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("---\nname: greeting\n---\nHello, {name}!")

    template = await manager.load(prompt_file)

    assert template.name == "greeting"
    assert template.content == "Hello, {name}!"


async def test_load_extracts_cacheable_and_cache_boundary(
    manager: PromptManager, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    # "content here" is 12 characters -- 7 is a real, valid boundary, not
    # the old test's out-of-range 100 (kept passing verbatim before
    # contracts/prompts.md open item 4's construction-time validation
    # existed to reject it).
    prompt_file.write_text(
        "---\nname: system-prompt\ncacheable: true\ncache_boundary: 7\n---\ncontent here"
    )

    template = await manager.load(prompt_file)

    assert template.cacheable is True
    assert template.cache_boundary == 7


async def test_load_puts_extra_frontmatter_fields_into_metadata(
    manager: PromptManager, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("---\nname: greeting\nauthor: devon\ntags: onboarding\n---\nHi!")

    template = await manager.load(prompt_file)

    assert template.metadata == {"author": "devon", "tags": "onboarding"}


async def test_load_raises_on_missing_frontmatter(manager: PromptManager, tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("just plain content, no frontmatter")

    with pytest.raises(PromptParseError):
        await manager.load(prompt_file)


async def test_load_raises_on_missing_name(manager: PromptManager, tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("---\ndescription: no name field\n---\ncontent")

    with pytest.raises(PromptParseError):
        await manager.load(prompt_file)


async def test_load_is_idempotent_for_unchanged_content(
    manager: PromptManager, tmp_path: Path
) -> None:
    """Avoids version-number churn from repeated CI loads of an unchanged
    file -- mirrors ToolManager.register()'s idempotency.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("---\nname: greeting\n---\nHello!")

    first = await manager.load(prompt_file)
    second = await manager.load(prompt_file)

    assert first.version == second.version == 1


async def test_load_creates_new_version_when_content_changes(
    manager: PromptManager, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("---\nname: greeting\n---\nHello!")
    await manager.load(prompt_file)

    prompt_file.write_text("---\nname: greeting\n---\nHello there!")
    second = await manager.load(prompt_file)

    assert second.version == 2
