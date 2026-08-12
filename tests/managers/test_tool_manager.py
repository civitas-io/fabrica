"""Tests for ToolManager -- including a real end-to-end run through
SubprocessSandbox, not just mocked orchestration, to prove the whole
composed stack actually works together.
"""

from __future__ import annotations

import pytest

from fabrica.managers import GrantDeniedError, ToolManager
from fabrica.presidium import GrantResult
from fabrica.retriever import KeywordBackend, Retriever
from fabrica.sandbox import SandboxPool, SubprocessSandbox
from fabrica.scope import Scope
from fabrica.tools import DictToolNamespace, ToolSchema


class _AllowClient:
    async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
        return GrantResult(decision="allow")


class _DenyClient:
    async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
        return GrantResult(decision="deny", reason="test denial")


def make_weather_namespace() -> DictToolNamespace:
    def get_weather(city: str) -> dict[str, str | int]:
        return {"city": city, "temperature_c": 22}

    return DictToolNamespace(
        {
            "get_weather": (
                ToolSchema(
                    name="get_weather",
                    description="get the current weather for a city",
                    input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
                ),
                get_weather,
            )
        }
    )


@pytest.fixture
def tool_manager() -> ToolManager:
    retriever = Retriever(primary=KeywordBackend())
    sandbox_pool = SandboxPool(SubprocessSandbox(), warm_size=0, max_concurrent=5)
    return ToolManager(retriever, sandbox_pool, _AllowClient())


def test_tier_delegates_to_sandbox_pool(tool_manager: ToolManager) -> None:
    assert tool_manager.tier == 0  # SubprocessSandbox is Tier 0


async def test_register_then_find(tool_manager: ToolManager) -> None:
    await tool_manager.register(make_weather_namespace())

    results = await tool_manager.find("weather")

    assert len(results) == 1
    assert results[0].item.name == "get_weather"
    assert results[0].item.kind == "tool"


async def test_register_propagates_eager_flag_from_tool_schema() -> None:
    # A dedicated retriever/tool_manager pair (not the shared fixture) so
    # the test can inspect list_eager() on the actual Retriever instance
    # ToolManager.register() wrote into.
    retriever = Retriever(primary=KeywordBackend())
    sandbox_pool = SandboxPool(SubprocessSandbox(), warm_size=0, max_concurrent=5)
    tool_manager = ToolManager(retriever, sandbox_pool, _AllowClient())

    def always_on() -> str:
        return "on"

    namespace = DictToolNamespace(
        {
            "always_on": (
                ToolSchema(
                    name="always_on", description="always visible", input_schema={}, eager=True
                ),
                always_on,
            ),
            "get_weather": (
                ToolSchema(name="get_weather", description="not eager", input_schema={}),
                always_on,
            ),
        }
    )
    await tool_manager.register(namespace)

    eager_items = await retriever.list_eager(kind="tool")
    eager_names = {item.name for item in eager_items}
    assert eager_names == {"always_on"}  # get_weather's default eager=False excluded


async def test_run_code_executes_real_tool_call_end_to_end(tool_manager: ToolManager) -> None:
    """The real proof: generated code calls namespace.call(), which
    crosses ToolManager's on_tool_call bridge, into the ACTUAL registered
    Python function, through a REAL subprocess/ZMQ boundary -- nothing
    mocked in this path.
    """
    await tool_manager.register(make_weather_namespace())

    code = """
result = namespace.call("get_weather", {"city": "Lisbon"})
print(f"{result['value']['city']}: {result['value']['temperature_c']}C")
"""
    run_result = await tool_manager.run_code(
        code, agent_id="agent-1", scope=Scope(agent_id="agent-1")
    )

    assert run_result.success is True
    assert run_result.stdout.strip() == "Lisbon: 22C"
    assert run_result.tool_call_count == 1


async def test_run_code_denied_raises_before_acquiring_sandbox() -> None:
    retriever = Retriever(primary=KeywordBackend())
    sandbox_pool = SandboxPool(SubprocessSandbox(), warm_size=0, max_concurrent=5)
    tool_manager = ToolManager(retriever, sandbox_pool, _DenyClient())

    with pytest.raises(GrantDeniedError):
        await tool_manager.run_code("print(1)", agent_id="agent-1", scope=Scope())


async def test_on_tool_call_reports_unknown_tool_as_routine_error(
    tool_manager: ToolManager,
) -> None:
    await tool_manager.register(make_weather_namespace())

    code = """
result = namespace.call("nonexistent_tool", {})
print(result["error_message"])
"""
    run_result = await tool_manager.run_code(
        code, agent_id="agent-1", scope=Scope(agent_id="agent-1")
    )

    assert run_result.success is True  # the CODE ran fine and printed cleanly
    assert "unknown tool" in run_result.stdout
