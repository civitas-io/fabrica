"""End-to-end proof that real span emission (system-design.md §7) actually
works across the REAL, fully-assembled object graph -- not just at each
component in isolation (see the per-component tests in tests/managers/,
tests/retriever/, tests/sandbox/, tests/memory/). Builds a real Fabrica
via CivitasBridge, exercises find()/run_code()/memory.write()/search(),
and asserts on exact span names, nesting (trace_id/parent_span_id
linkage), and attributes -- not just "something got logged."
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fabrica.civitas_bridge import CivitasBridge
from fabrica.memory.types import MemoryItem
from fabrica.scope import Scope
from fabrica.tools import DictToolNamespace, ToolSchema


class _RecordingSpan:
    def __init__(self, name: str, trace_id: str, span_id: str, parent_span_id: str | None) -> None:
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.attributes: dict[str, Any] = {}
        self.error: BaseException | None = None
        self.ended = False

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_error(self, exc: BaseException) -> None:
        self.error = exc

    def end(self) -> None:
        self.ended = True


class _RecordingTracer:
    def __init__(self) -> None:
        self.spans: list[_RecordingSpan] = []
        self._counter = 0

    def start_span(
        self,
        name: str,
        trace_id: str = "",
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> _RecordingSpan:
        self._counter += 1
        span = _RecordingSpan(
            name, trace_id or f"trace-{self._counter}", f"span-{self._counter}", parent_span_id
        )
        span.attributes.update(attributes or {})
        self.spans.append(span)
        return span

    def by_name(self, name: str) -> _RecordingSpan:
        matches = [s for s in self.spans if s.name == name]
        assert len(matches) == 1, f"expected exactly one {name!r} span, got {len(matches)}"
        return matches[0]


def make_add_namespace() -> DictToolNamespace:
    def add(a: int, b: int) -> int:
        return a + b

    return DictToolNamespace(
        {
            "add": (
                ToolSchema(
                    name="add",
                    description="add two integers",
                    input_schema={
                        "type": "object",
                        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    },
                ),
                add,
            )
        }
    )


@pytest.fixture
def tracer() -> _RecordingTracer:
    return _RecordingTracer()


async def test_tool_find_emits_a_nested_tool_find_and_retriever_search_span(
    tracer: _RecordingTracer,
) -> None:
    fabrica = await CivitasBridge(allow_ungoverned=True, tracer=tracer).build()
    await fabrica.tools.register(make_add_namespace())

    await fabrica.tools.find("add two numbers")

    find_span = tracer.by_name("fabrica.tool.find")
    search_span = tracer.by_name("fabrica.retriever.search")
    assert find_span.attributes["query"] == "add two numbers"
    assert find_span.attributes["result_count"] == 1
    assert isinstance(find_span.attributes["latency_ms"], float)
    assert find_span.ended is True
    # Real nesting, not two disconnected spans sharing a name prefix.
    assert search_span.trace_id == find_span.trace_id
    assert search_span.parent_span_id == find_span.span_id
    assert search_span.ended is True

    await fabrica.close()


async def test_run_code_emits_the_full_nested_span_tree(tracer: _RecordingTracer) -> None:
    fabrica = await CivitasBridge(allow_ungoverned=True, tracer=tracer).build()
    await fabrica.tools.register(make_add_namespace())

    result = await fabrica.tools.run_code(
        "result = namespace.call('add', {'a': 2, 'b': 3})\nprint(result['value'])\n",
        agent_id="agent-1",
        scope=Scope(agent_id="agent-1", user_id="u1", session_id="s1", team_id="t1"),
    )
    assert result.success is True

    outer = tracer.by_name("fabrica.tool.code_mode.run")
    grant = tracer.by_name("fabrica.presidium.check_grant")
    acquire = tracer.by_name("fabrica.sandbox.acquire")
    run = tracer.by_name("fabrica.sandbox.run")

    # Every child nests directly under the same outer span -- one real
    # tree, not four spans that merely share a naming convention.
    for child in (grant, acquire, run):
        assert child.trace_id == outer.trace_id
        assert child.parent_span_id == outer.span_id
        assert child.ended is True

    assert outer.attributes["agent_id"] == "agent-1"
    assert outer.attributes["user_id"] == "u1"
    assert outer.attributes["session_id"] == "s1"
    assert outer.attributes["team_id"] == "t1"
    assert "code_hash" in outer.attributes
    assert outer.attributes["tool_call_count"] == 1
    assert grant.attributes["decision"] == "allow"
    assert isinstance(grant.attributes["latency_ms"], float)
    assert acquire.attributes["tier"] == 0
    assert acquire.attributes["warm_hit"] is False
    assert run.attributes["exit_status"] == "ok"

    await fabrica.close()


async def test_a_denied_grant_still_ends_the_check_grant_span_before_raising(
    tracer: _RecordingTracer,
) -> None:
    from fabrica.managers import GrantDeniedError
    from fabrica.presidium import GrantResult

    class _DenyingClient:
        async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
            return GrantResult(decision="deny", reason="no")

    fabrica = await CivitasBridge(
        allow_ungoverned=True, presidium_client=_DenyingClient(), tracer=tracer
    ).build()

    with pytest.raises(GrantDeniedError):
        await fabrica.tools.run_code("1 + 1", agent_id="a", scope=Scope())

    grant = tracer.by_name("fabrica.presidium.check_grant")
    assert grant.attributes["decision"] == "deny"
    assert grant.ended is True
    # A real code-mode.run outer span still exists and recorded the
    # GrantDeniedError via set_error() -- traced() never swallows errors.
    outer = tracer.by_name("fabrica.tool.code_mode.run")
    assert outer.ended is True
    assert isinstance(outer.error, GrantDeniedError)

    await fabrica.close()


async def test_skill_run_emits_a_span_with_skill_name(
    tracer: _RecordingTracer, tmp_path: Path
) -> None:
    skill_dir = tmp_path / "greeter"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: greeter\ndescription: greets someone\nscript: scripts/run.py\n---\n"
    )
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print(f\"Hello, {args['name']}!\")")

    fabrica = await CivitasBridge(allow_ungoverned=True, tracer=tracer).build()
    await fabrica.skills.load(skill_dir)

    result = await fabrica.skills.run(
        "greeter", {"name": "Priya"}, agent_id="a1", scope=Scope(agent_id="a1")
    )
    assert result.success is True

    skill_run_span = tracer.by_name("fabrica.skill.run")
    assert skill_run_span.attributes["skill_name"] == "greeter"
    assert skill_run_span.attributes["agent_id"] == "a1"
    # code_mode.run's outer span never fired -- this is the OTHER branch
    # of execute_in_sandbox's shared span_name logic.
    assert not any(s.name == "fabrica.tool.code_mode.run" for s in tracer.spans)

    await fabrica.close()


async def test_an_over_budget_deny_refuses_the_run_before_touching_the_sandbox_at_all(
    tracer: _RecordingTracer,
) -> None:
    """The real proof behind civitas-presidium-integration.md's usage/
    budget-ceilings design: 'Fabrica ONLY checks before executing... if
    Presidium's policy engine already flags a scope as over-budget,
    Fabrica refuses the run before it starts.' No new Fabrica mechanism
    exists for this -- a real PresidiumClient returning `deny` for ANY
    reason (an over-budget scope included) already refuses the run via
    the exact same check_grant() gate proven in the grant-denial test
    above. This test names the budget scenario explicitly and proves the
    sandbox is never touched at all -- zero fabrica.sandbox.* spans,
    zero real compute consumed -- not just that an exception was raised.
    """
    from fabrica.managers import GrantDeniedError
    from fabrica.presidium import GrantResult

    class _OverBudgetPresidiumClient:
        """Stands in for a real PresidiumClient whose policy engine has
        already flagged this scope as over-budget -- indistinguishable,
        from Fabrica's side, from any other deny (system-design.md's own
        point: no new decision type, the same ALLOW/DENY/REQUIRE_APPROVAL
        shape).
        """

        async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
            return GrantResult(decision="deny", reason="team t1 is over its sandbox-cpu budget")

    fabrica = await CivitasBridge(
        allow_ungoverned=True, presidium_client=_OverBudgetPresidiumClient(), tracer=tracer
    ).build()
    await fabrica.tools.register(make_add_namespace())

    with pytest.raises(GrantDeniedError, match="over its sandbox-cpu budget"):
        await fabrica.tools.run_code(
            "namespace.call('add', {'a': 1, 'b': 1})",
            agent_id="agent-1",
            scope=Scope(team_id="t1"),
        )

    # The whole point: no sandbox.acquire, no sandbox.run -- the refusal
    # happened before any real resource was ever touched.
    assert not any(s.name.startswith("fabrica.sandbox.") for s in tracer.spans)
    grant_span = tracer.by_name("fabrica.presidium.check_grant")
    assert grant_span.attributes["decision"] == "deny"

    await fabrica.close()


async def test_memory_write_and_search_emit_real_spans_with_scope_attributes(
    tracer: _RecordingTracer,
) -> None:
    fabrica = await CivitasBridge(allow_ungoverned=True, tracer=tracer).build()
    scope = Scope(user_id="u1", session_id="s1", agent_id="a1", team_id="t1")

    await fabrica.memory.write(scope, MemoryItem(id=None, content="the sky is blue"))
    await fabrica.memory.search(scope, "sky")

    write_span = tracer.by_name("fabrica.memory.write")
    search_span = tracer.by_name("fabrica.memory.search")
    for span in (write_span, search_span):
        assert span.attributes["user_id"] == "u1"
        assert span.attributes["session_id"] == "s1"
        assert span.attributes["agent_id"] == "a1"
        assert span.attributes["team_id"] == "t1"
        assert span.ended is True
    assert "memory_id" in write_span.attributes
    assert write_span.attributes["volume_bytes"] == len(b"the sky is blue")
    assert search_span.attributes["result_count"] == 1
    assert search_span.attributes["volume_bytes"] == len(b"the sky is blue")

    await fabrica.close()
