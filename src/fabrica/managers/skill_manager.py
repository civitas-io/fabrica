"""SkillManager -- see docs/contracts/managers.md.

SKILL.md parsing here covers frontmatter validation to the specific cases
the contract names (missing required fields, name charset, description
length) -- not the full bundled scripts/assets/references progressive-
disclosure surface, which skills-gateway.md's real-spec check found zero
bigpowers skills actually exercising. An optional `script` frontmatter
field is supported as the executable entry point a skill can bundle; a
skill without one is find()-discoverable but not run()-able, since there
is nothing to actually execute for a purely instructional skill.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from fabrica.managers.errors import SkillNotFoundError, SkillParseError
from fabrica.managers.execute_in_sandbox import execute_in_sandbox
from fabrica.observability import NullTracer, Tracer, traced
from fabrica.presidium import PresidiumClient
from fabrica.retriever import Indexable, RankedMatch, Retriever
from fabrica.sandbox import RunResult, SandboxPool
from fabrica.scope import Scope

_MAX_DESCRIPTION_LENGTH = 1024
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class _RegisteredSkill:
    name: str
    description: str
    script: str | None
    skill_dir: Path


class SkillManager:
    def __init__(
        self,
        retriever: Retriever,
        sandbox_pool: SandboxPool,
        presidium_client: PresidiumClient,
        *,
        tracer: Tracer | None = None,
    ) -> None:
        self._retriever = retriever
        self._sandbox_pool = sandbox_pool
        self._presidium_client = presidium_client
        self._skills: dict[str, _RegisteredSkill] = {}
        # `tracer` emits `fabrica.skill.find`/`fabrica.skill.run`
        # (system-design.md §7) -- defaults to NullTracer(), a real no-op,
        # matching the NullPresidiumClient/NullCompactor DI pattern.
        self._tracer = tracer if tracer is not None else NullTracer()

    @property
    def tier(self) -> int:
        """Delegates to the underlying SandboxPool -- same rationale as
        ToolManager.tier (contracts/mcp-server.md's WeakIsolationError).
        """
        return self._sandbox_pool.tier

    async def load(self, skill_dir: Path) -> None:
        """Parses SKILL.md's frontmatter, registers as Indexable(kind="skill").

        Raises:
            SkillParseError: malformed frontmatter.
        """
        skill_md = skill_dir / "SKILL.md"
        try:
            raw = skill_md.read_text()
        except OSError as exc:
            raise SkillParseError(f"cannot read {skill_md}: {exc}") from exc

        match = _FRONTMATTER_RE.match(raw)
        if match is None:
            raise SkillParseError(f"{skill_md}: no YAML frontmatter block found")

        try:
            frontmatter = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise SkillParseError(f"{skill_md}: malformed YAML frontmatter: {exc}") from exc

        if not isinstance(frontmatter, dict):
            raise SkillParseError(f"{skill_md}: frontmatter must be a mapping")

        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if not name or not isinstance(name, str):
            raise SkillParseError(f"{skill_md}: missing required field 'name'")
        if not description or not isinstance(description, str):
            raise SkillParseError(f"{skill_md}: missing required field 'description'")
        if not _NAME_RE.match(name):
            raise SkillParseError(
                f"{skill_md}: name {name!r} must be lowercase alphanumeric and hyphens only"
            )
        if len(description) > _MAX_DESCRIPTION_LENGTH:
            raise SkillParseError(
                f"{skill_md}: description is {len(description)} chars, "
                f"exceeds the {_MAX_DESCRIPTION_LENGTH}-char limit"
            )

        script = frontmatter.get("script")
        if script is not None and not isinstance(script, str):
            raise SkillParseError(f"{skill_md}: 'script' field must be a string path")

        eager = frontmatter.get("eager", False)
        if not isinstance(eager, bool):
            raise SkillParseError(f"{skill_md}: 'eager' field must be a boolean")

        self._skills[name] = _RegisteredSkill(
            name=name, description=description, script=script, skill_dir=skill_dir
        )
        await self._retriever.register(
            [Indexable(id=name, kind="skill", name=name, description=description, eager=eager)]
        )

    async def find(self, query: str, *, limit: int = 5) -> list[RankedMatch]:
        """Thin delegation -- same shape as ToolManager.find(), different
        kind -- plus its own fabrica.skill.find span nesting the
        underlying fabrica.retriever.search span, same pattern as
        ToolManager.find().
        """
        with traced(self._tracer, "fabrica.skill.find", query=query, kind="skill") as span:
            start = time.monotonic()
            results = await self._retriever.search(
                query,
                kind="skill",
                limit=limit,
                trace_id=span.trace_id,
                parent_span_id=span.span_id,
            )
            span.set_attribute("result_count", len(results))
            span.set_attribute("latency_ms", round((time.monotonic() - start) * 1000, 2))
            # Same context-footprint dimension as ToolManager.find() --
            # see its own comment for the reasoning (description-only,
            # matching MemoryManager's precedent of measuring one
            # designated content field, not a full serialized object).
            span.set_attribute(
                "volume_bytes", sum(len(m.item.description.encode()) for m in results)
            )
            return results

    async def run(
        self,
        name: str,
        args: dict[str, Any],
        *,
        agent_id: str,
        scope: Scope,
        timeout: float = 30.0,
        tool_call_timeout: float | None = None,
    ) -> RunResult:
        """Runs a NAMED, pre-written, author-trusted script -- not
        arbitrary generated code, per system-design.md §1's genuine
        distinction from run_code().

        `tool_call_timeout` is threaded through for API symmetry with
        `ToolManager.run_code()` (contracts/sandbox.md open item 3) --
        currently inert here, since this manager's own `on_tool_call`
        always returns instantly (skills have no tool access in this
        pass); kept so a future skill implementation with real tool
        access doesn't need a signature change to gain it.

        Raises:
            SkillNotFoundError: name isn't registered, or has no bundled
                script to execute (find()-discoverable, not run()-able).
        """
        skill = self._skills.get(name)
        if skill is None:
            raise SkillNotFoundError(f"no registered skill named {name!r}")
        if skill.script is None:
            raise SkillNotFoundError(
                f"skill {name!r} has no bundled 'script' to run -- it is find()-discoverable only"
            )

        script_body = (skill.skill_dir / skill.script).read_text()
        code = f"args = {json.dumps(args)}\n{script_body}"

        async def on_tool_call(tool: str, params: dict[str, Any]) -> dict[str, Any]:
            # Skills execute pre-written scripts, not code that calls
            # arbitrary registered tools by name the way code-mode does --
            # no tool namespace is wired here. A skill needing real tool
            # access would need one, not designed in this pass.
            return {"success": False, "value": None, "error_message": "skills have no tool access"}

        return await execute_in_sandbox(
            presidium_client=self._presidium_client,
            sandbox_pool=self._sandbox_pool,
            action=f"skill_run:{name}",
            agent_id=agent_id,
            scope=scope,
            code=code,
            on_tool_call=on_tool_call,
            timeout=timeout,
            tool_call_timeout=tool_call_timeout,
            tracer=self._tracer,
            skill_name=name,
        )
