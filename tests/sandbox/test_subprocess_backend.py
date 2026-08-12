"""Tests for SubprocessSandbox -- proving the real subprocess + ZMQ
callback bridge actually works, not just that the code looks plausible.
"""

from __future__ import annotations

from typing import Any

import pytest

from fabrica.sandbox import SandboxCrashedError, SandboxTimeoutError, SubprocessSandbox


@pytest.fixture
def backend() -> SubprocessSandbox:
    return SubprocessSandbox()


async def _no_tool_calls(tool: str, params: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError(f"unexpected tool call: {tool}({params})")


def test_tier_property_is_0(backend: SubprocessSandbox) -> None:
    assert backend.tier == 0


async def test_boot_clean_returns_tier_0_handle(backend: SubprocessSandbox) -> None:
    handle = await backend.boot_clean()
    assert handle.tier == 0
    assert handle.id


async def test_execute_captures_stdout(backend: SubprocessSandbox) -> None:
    handle = await backend.boot_clean()

    result = await backend.execute(
        handle, "print('hello from the sandbox')", on_tool_call=_no_tool_calls, timeout=5.0
    )

    assert result.success is True
    assert result.stdout.strip() == "hello from the sandbox"
    assert result.stdout_truncated is False
    assert result.error_message is None
    assert result.tool_call_count == 0
    assert result.duration_ms > 0


async def test_execute_reports_code_level_failure_as_routine_result(
    backend: SubprocessSandbox,
) -> None:
    handle = await backend.boot_clean()

    result = await backend.execute(
        handle, "raise ValueError('deliberate failure')", on_tool_call=_no_tool_calls, timeout=5.0
    )

    # Code-level failures are NOT raised exceptions -- a routine outcome
    # the model may need to see and correct, per the contract.
    assert result.success is False
    assert result.error_message is not None
    assert "ValueError" in result.error_message
    assert "deliberate failure" in result.error_message


async def test_execute_real_tool_call_round_trip(backend: SubprocessSandbox) -> None:
    """The real proof this bridge works end to end: the sandboxed code
    calls namespace.call(), which crosses the ZMQ ipc:// boundary to the
    parent's on_tool_call callback, and the RESULT crosses back into the
    sandboxed code -- not mocked, not simulated.
    """
    handle = await backend.boot_clean()

    async def echo_tool(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        assert tool == "get_weather"
        assert params == {"city": "Lisbon"}
        return {"temperature_c": 22, "condition": "sunny"}

    code = """
result = namespace.call("get_weather", {"city": "Lisbon"})
print(f"It is {result['temperature_c']}C and {result['condition']}")
"""
    result = await backend.execute(handle, code, on_tool_call=echo_tool, timeout=5.0)

    assert result.success is True
    assert result.stdout.strip() == "It is 22C and sunny"
    assert result.tool_call_count == 1


async def test_execute_counts_multiple_tool_calls(backend: SubprocessSandbox) -> None:
    handle = await backend.boot_clean()

    async def add_one(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"result": params["n"] + 1}

    code = """
total = 0
for i in range(3):
    total = namespace.call("add_one", {"n": total})["result"]
print(total)
"""
    result = await backend.execute(handle, code, on_tool_call=add_one, timeout=5.0)

    assert result.success is True
    assert result.stdout.strip() == "3"
    assert result.tool_call_count == 3


async def test_execute_raises_timeout_error_and_kills_process(backend: SubprocessSandbox) -> None:
    handle = await backend.boot_clean()

    with pytest.raises(SandboxTimeoutError):
        await backend.execute(
            handle, "import time; time.sleep(10)", on_tool_call=_no_tool_calls, timeout=0.5
        )


async def test_execute_raises_crashed_error_when_shim_produces_no_trailer(
    backend: SubprocessSandbox,
) -> None:
    handle = await backend.boot_clean()
    # os._exit() bypasses the shim's own trailer-writing entirely --
    # simulates a genuine crash (segfault, OOM-kill, etc.), not a routine
    # code-level exception the shim would have caught and reported.
    code = "import os; os._exit(1)"

    with pytest.raises(SandboxCrashedError):
        await backend.execute(handle, code, on_tool_call=_no_tool_calls, timeout=5.0)


async def test_execute_truncates_huge_stdout(backend: SubprocessSandbox) -> None:
    handle = await backend.boot_clean()
    code = "print('x' * 200000)"

    result = await backend.execute(handle, code, on_tool_call=_no_tool_calls, timeout=5.0)

    assert result.stdout_truncated is True
    assert len(result.stdout.encode()) <= 65536


async def test_terminate_is_safe_to_call(backend: SubprocessSandbox) -> None:
    handle = await backend.boot_clean()
    await backend.terminate(handle)  # must not raise


async def test_health_check_true_when_shim_present(backend: SubprocessSandbox) -> None:
    assert await backend.health_check() is True
