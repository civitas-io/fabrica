"""Tests for SrtSandbox -- REAL srt (Anthropic Sandbox Runtime), real OS-
level network/filesystem enforcement. Not mocked anywhere in this path,
same discipline as test_firecracker_backend.py. Skipped when `srt` isn't
on PATH.

The single most important test in this file is
test_execute_blocks_network_to_non_allowlisted_domain -- it's the actual
proof Milestone 1's "genuinely unreachable, not just policy-disallowed"
requirement holds, not an assumption from srt's own README.

Every test that constructs its own SrtSandbox (rather than using the
`backend` fixture below) is responsible for its own close() -- the
`backend` fixture exists specifically so most tests don't have to think
about this, closing real instance-level resources deterministically
instead of relying on this file's own tests to remember, matching the
real leak found and fixed in Sandbox.close()'s own docstring.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from fabrica.sandbox.network_policy import NetworkPolicy
from fabrica.sandbox.srt_backend import SrtSandbox, srt_available

pytestmark = pytest.mark.skipif(not srt_available(), reason="requires real srt on PATH")


@pytest.fixture
async def backend() -> AsyncIterator[SrtSandbox]:
    instance = SrtSandbox(NetworkPolicy())
    yield instance
    await instance.close()


async def _no_tool_calls(tool: str, params: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError(f"unexpected tool call: {tool}({params})")


def test_tier_property_is_1(backend: SrtSandbox) -> None:
    assert backend.tier == 1


async def test_health_check_true_when_srt_available(backend: SrtSandbox) -> None:
    assert await backend.health_check() is True


async def test_boot_clean_returns_tier_1_handle(backend: SrtSandbox) -> None:
    handle = await backend.boot_clean()
    assert handle.tier == 1
    assert handle.id


async def test_execute_captures_stdout_with_no_network_needed(backend: SrtSandbox) -> None:
    # Empty policy (deny all network) must not block ordinary code
    # execution -- only network access.
    handle = await backend.boot_clean()

    result = await backend.execute(
        handle, "print('hello from srt')", on_tool_call=_no_tool_calls, timeout=15.0
    )

    assert result.success is True
    assert result.stdout.strip() == "hello from srt"


async def test_execute_real_tool_call_round_trip(backend: SrtSandbox) -> None:
    """The real proof the guest-shim/ZMQ bridge still works when wrapped
    in srt: the sandboxed code calls namespace.call(), crossing the ZMQ
    ipc:// boundary to the parent's on_tool_call callback -- via the
    exact per-call `allowUnixSockets` allowlist entry SrtSandbox writes,
    not a general Unix-socket allowance.
    """
    handle = await backend.boot_clean()

    async def echo_tool(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"success": True, "value": {"echo": params}, "error_message": None}

    result = await backend.execute(
        handle,
        "result = namespace.call('echo', {'x': 1}); print(result['value']['echo'])",
        on_tool_call=echo_tool,
        timeout=15.0,
    )

    assert result.success is True
    assert "{'x': 1}" in result.stdout


async def test_execute_allows_network_to_allowlisted_domain() -> None:
    # Needs its own backend -- not the default-deny `backend` fixture.
    backend = SrtSandbox(NetworkPolicy.from_scope_hosts(["example.com"]))
    try:
        handle = await backend.boot_clean()

        code = (
            "import urllib.request\n"
            "resp = urllib.request.urlopen('https://example.com', timeout=10)\n"
            "print('STATUS', resp.status)\n"
        )
        result = await backend.execute(handle, code, on_tool_call=_no_tool_calls, timeout=20.0)

        assert result.success is True, result.error_message
        assert "STATUS 200" in result.stdout
    finally:
        await backend.close()


async def test_execute_blocks_network_to_non_allowlisted_domain() -> None:
    """The single most important test in this file: an out-of-scope host
    is genuinely unreachable at the OS level -- not something the
    sandboxed code could talk its way around, and not merely
    policy-disallowed at an application layer the code never touches."""
    backend = SrtSandbox(NetworkPolicy.from_scope_hosts(["example.com"]))
    try:
        handle = await backend.boot_clean()

        code = (
            "import urllib.request\n"
            "try:\n"
            "    urllib.request.urlopen('https://anthropic.com', timeout=10)\n"
            "    print('REACHED')\n"
            "except Exception as exc:\n"
            "    print('BLOCKED', type(exc).__name__)\n"
        )
        result = await backend.execute(handle, code, on_tool_call=_no_tool_calls, timeout=20.0)

        assert result.success is True, result.error_message
        assert "BLOCKED" in result.stdout
        assert "REACHED" not in result.stdout
    finally:
        await backend.close()


async def test_execute_empty_policy_blocks_all_network(backend: SrtSandbox) -> None:
    handle = await backend.boot_clean()

    code = (
        "import urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('https://example.com', timeout=10)\n"
        "    print('REACHED')\n"
        "except Exception as exc:\n"
        "    print('BLOCKED', type(exc).__name__)\n"
    )
    result = await backend.execute(handle, code, on_tool_call=_no_tool_calls, timeout=20.0)

    assert result.success is True, result.error_message
    assert "BLOCKED" in result.stdout


async def test_execute_denies_read_of_ssh_directory(backend: SrtSandbox) -> None:
    handle = await backend.boot_clean()

    code = (
        "import os\n"
        "try:\n"
        "    os.listdir(os.path.expanduser('~/.ssh'))\n"
        "    print('READ_SUCCEEDED')\n"
        "except Exception as exc:\n"
        "    print('READ_DENIED', type(exc).__name__)\n"
    )
    result = await backend.execute(handle, code, on_tool_call=_no_tool_calls, timeout=15.0)

    assert result.success is True, result.error_message
    assert "READ_DENIED" in result.stdout


async def test_execute_reports_code_level_failure_as_routine_result(backend: SrtSandbox) -> None:
    handle = await backend.boot_clean()

    result = await backend.execute(
        handle, "raise ValueError('deliberate failure')", on_tool_call=_no_tool_calls, timeout=15.0
    )

    assert result.success is False
    assert result.error_message is not None
    assert "ValueError" in result.error_message


async def test_execute_settings_file_is_cleaned_up_after_run(backend: SrtSandbox) -> None:
    handle = await backend.boot_clean()

    await backend.execute(handle, "print(1)", on_tool_call=_no_tool_calls, timeout=15.0)

    settings_path = backend._socket_dir / f"{handle.id}-settings.json"
    assert not settings_path.exists()


async def test_terminate_cleans_up_leftover_files(backend: SrtSandbox) -> None:
    handle = await backend.boot_clean()
    (backend._socket_dir / f"{handle.id}.sock").touch()
    (backend._socket_dir / f"{handle.id}-settings.json").touch()

    await backend.terminate(handle)

    assert not (backend._socket_dir / f"{handle.id}.sock").exists()
    assert not (backend._socket_dir / f"{handle.id}-settings.json").exists()


async def test_close_removes_the_instance_level_socket_directory() -> None:
    # The real, confirmed leak this proves fixed: __init__ creates this
    # directory, but terminate() (per-handle) never removed it -- nothing
    # did, until close() existed. Confirmed independently by inspecting
    # /tmp directly: hundreds had already accumulated before this fix.
    # Deliberately NOT the `backend` fixture -- this test owns close()
    # itself, asserting on the directory's existence before AND after.
    standalone_backend = SrtSandbox(NetworkPolicy())
    socket_dir = standalone_backend._socket_dir
    assert socket_dir.exists()

    await standalone_backend.close()

    assert not socket_dir.exists()


async def test_close_is_safe_even_if_never_used() -> None:
    # A backend constructed and closed without boot_clean()/execute() ever
    # being called must not raise -- Sandbox.close()'s own contract.
    standalone_backend = SrtSandbox(NetworkPolicy())
    await standalone_backend.close()  # must not raise


async def test_close_is_safe_to_call_twice() -> None:
    standalone_backend = SrtSandbox(NetworkPolicy())
    await standalone_backend.close()
    await standalone_backend.close()  # must not raise on an already-removed directory
