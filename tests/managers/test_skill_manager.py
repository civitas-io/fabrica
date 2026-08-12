"""Tests for SkillManager -- real SKILL.md parsing and execution, not
mocked frontmatter or a fake sandbox.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fabrica.managers import SkillManager, SkillNotFoundError, SkillParseError
from fabrica.presidium import GrantResult
from fabrica.retriever import KeywordBackend, Retriever
from fabrica.sandbox import SandboxPool, SubprocessSandbox
from fabrica.scope import Scope


class _AllowClient:
    async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
        return GrantResult(decision="allow")


@pytest.fixture
def skill_manager() -> SkillManager:
    retriever = Retriever(primary=KeywordBackend())
    sandbox_pool = SandboxPool(SubprocessSandbox(), warm_size=0, max_concurrent=5)
    return SkillManager(retriever, sandbox_pool, _AllowClient())


def write_skill(tmp_path: Path, name: str, frontmatter_extra: str = "", body: str = "") -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: a test skill\n{frontmatter_extra}---\n{body}"
    )
    return skill_dir


def test_tier_delegates_to_sandbox_pool(skill_manager: SkillManager) -> None:
    assert skill_manager.tier == 0  # SubprocessSandbox is Tier 0


async def test_load_then_find(skill_manager: SkillManager, tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, "greet-user")

    await skill_manager.load(skill_dir)
    results = await skill_manager.find("test skill")

    assert len(results) == 1
    assert results[0].item.name == "greet-user"
    assert results[0].item.kind == "skill"


async def test_load_raises_on_missing_frontmatter(
    skill_manager: SkillManager, tmp_path: Path
) -> None:
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("just a plain markdown file, no frontmatter")

    with pytest.raises(SkillParseError):
        await skill_manager.load(skill_dir)


async def test_load_raises_on_missing_required_field(
    skill_manager: SkillManager, tmp_path: Path
) -> None:
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: bad-skill\n---\nno description field")

    with pytest.raises(SkillParseError):
        await skill_manager.load(skill_dir)


async def test_load_raises_on_invalid_name_charset(
    skill_manager: SkillManager, tmp_path: Path
) -> None:
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: Bad Skill Name!\ndescription: x\n---\n")

    with pytest.raises(SkillParseError):
        await skill_manager.load(skill_dir)


async def test_load_raises_on_description_too_long(
    skill_manager: SkillManager, tmp_path: Path
) -> None:
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    long_description = "x" * 1025
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: bad-skill\ndescription: {long_description}\n---\n"
    )

    with pytest.raises(SkillParseError):
        await skill_manager.load(skill_dir)


async def test_run_raises_not_found_for_unregistered_skill(skill_manager: SkillManager) -> None:
    with pytest.raises(SkillNotFoundError):
        await skill_manager.run("nonexistent", {}, agent_id="a1", scope=Scope())


async def test_run_raises_not_found_when_skill_has_no_script(
    skill_manager: SkillManager, tmp_path: Path
) -> None:
    skill_dir = write_skill(tmp_path, "instructions-only")
    await skill_manager.load(skill_dir)

    with pytest.raises(SkillNotFoundError):
        await skill_manager.run("instructions-only", {}, agent_id="a1", scope=Scope())


async def test_run_executes_bundled_script_with_args(
    skill_manager: SkillManager, tmp_path: Path
) -> None:
    skill_dir = write_skill(tmp_path, "greeter", frontmatter_extra="script: scripts/run.py\n")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print(f\"Hello, {args['name']}!\")")
    await skill_manager.load(skill_dir)

    result = await skill_manager.run(
        "greeter", {"name": "Priya"}, agent_id="a1", scope=Scope(agent_id="a1")
    )

    assert result.success is True
    assert result.stdout.strip() == "Hello, Priya!"
