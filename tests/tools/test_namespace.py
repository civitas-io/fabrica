"""Tests for DictToolNamespace directly -- not just exercised indirectly
through ToolManager.
"""

from __future__ import annotations

from fabrica.tools import DictToolNamespace, ToolSchema


def make_namespace() -> DictToolNamespace:
    def add(a: int, b: int) -> int:
        return a + b

    async def async_multiply(a: int, b: int) -> int:
        return a * b

    return DictToolNamespace(
        {
            "add": (
                ToolSchema(name="add", description="add two numbers", input_schema={}),
                add,
            ),
            "multiply": (
                ToolSchema(name="multiply", description="multiply two numbers", input_schema={}),
                async_multiply,
            ),
        }
    )


def test_list_schemas_returns_all_registered_tools() -> None:
    namespace = make_namespace()
    names = {schema.name for schema in namespace.list_schemas()}
    assert names == {"add", "multiply"}


def test_open_returns_schema_by_name() -> None:
    namespace = make_namespace()
    schema = namespace.open("add")
    assert schema.description == "add two numbers"


def test_stubs_lists_descriptions() -> None:
    namespace = make_namespace()
    stubs = namespace.stubs()
    assert "add" in stubs
    assert "multiply" in stubs


async def test_call_sync_function() -> None:
    namespace = make_namespace()
    result = await namespace.call("add", {"a": 2, "b": 3})
    assert result.success is True
    assert result.value == 5


async def test_call_async_function() -> None:
    namespace = make_namespace()
    result = await namespace.call("multiply", {"a": 4, "b": 5})
    assert result.success is True
    assert result.value == 20


async def test_call_unknown_tool_returns_routine_error_not_exception() -> None:
    namespace = make_namespace()
    result = await namespace.call("nonexistent", {})
    assert result.success is False
    assert "unknown tool" in result.error_message


async def test_call_function_that_raises_returns_routine_error() -> None:
    def broken(x: int) -> int:
        raise ValueError("deliberate failure")

    namespace = DictToolNamespace(
        {"broken": (ToolSchema(name="broken", description="", input_schema={}), broken)}
    )

    result = await namespace.call("broken", {"x": 1})

    assert result.success is False
    assert "deliberate failure" in result.error_message
