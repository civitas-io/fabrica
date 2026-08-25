"""Tests for RestPresidiumClient against httpx.MockTransport -- deterministic,
fast, no real server needed. The real, end-to-end mTLS proof against an
actual running Presidium server lives in
test_rest_client_real_presidium_server.py.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from fabrica.presidium.rest_client import RestPresidiumClient
from fabrica.scope import Scope


def _client(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: object
) -> RestPresidiumClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return RestPresidiumClient(base_url="https://presidium.example", client=http_client, **kwargs)  # type: ignore[arg-type]


def _json_handler(
    status: int, body: dict[str, object]
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


class TestRealRequestShape:
    """Confirms the wire contract matches civitas-io/presidium's own real,
    shipped PresidiumGatewayAgent.handle_call() exactly -- not guessed."""

    async def test_posts_to_v1_check_grant_with_the_real_fields(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["body"] = _json.loads(request.content)
            return httpx.Response(
                200, json={"decision": "allow", "reason": None, "approval_context": None}
            )

        client = _client(handler)
        await client.check_grant(
            agent_id="presidium://acme.com/researcher", action="code_mode", scope=Scope()
        )

        assert captured["method"] == "POST"
        assert captured["url"] == "https://presidium.example/v1/check_grant"
        body = captured["body"]
        assert isinstance(body, dict)
        assert body["agent_id"] == "presidium://acme.com/researcher"
        assert body["action"] == "code_mode"
        assert body["scope"] == {}

    async def test_scope_fields_are_serialized_when_present(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            captured["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"decision": "allow"})

        client = _client(handler)
        await client.check_grant(
            agent_id="a",
            action="code_mode",
            scope=Scope(user_id="u1", session_id="s1", agent_id="a1", team_id="t1"),
        )

        body = captured["body"]
        assert isinstance(body, dict)
        assert body["scope"] == {
            "user_id": "u1",
            "session_id": "s1",
            "agent_id": "a1",
            "team_id": "t1",
        }

    async def test_none_scope_fields_are_omitted_not_sent_as_null(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            captured["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"decision": "allow"})

        client = _client(handler)
        await client.check_grant(agent_id="a", action="x", scope=Scope(user_id="u1"))

        body = captured["body"]
        assert isinstance(body, dict)
        assert body["scope"] == {"user_id": "u1"}
        assert "session_id" not in body["scope"]

    async def test_extra_fields_are_merged_flat_not_nested(self) -> None:
        """The real gap an external audit found: the wire protocol/server accept
        arbitrary scope keys (deserialized into ActionRequest.parameters), but
        Scope itself had no slot for them. extra={"target_host": ...} must show
        up as request.parameters.target_host to a CEL policy, not nested under
        an "extra" key."""
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            captured["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"decision": "allow"})

        client = _client(handler)
        await client.check_grant(
            agent_id="a",
            action="x",
            scope=Scope(agent_id="a1", extra={"target_host": "db1", "risk": "high"}),
        )

        body = captured["body"]
        assert isinstance(body, dict)
        assert body["scope"] == {
            "agent_id": "a1",
            "target_host": "db1",
            "risk": "high",
        }
        assert "extra" not in body["scope"]

    async def test_empty_extra_is_not_sent(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            captured["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"decision": "allow"})

        client = _client(handler)
        await client.check_grant(agent_id="a", action="x", scope=Scope())

        body = captured["body"]
        assert isinstance(body, dict)
        assert "extra" not in body["scope"]

    def test_extra_key_colliding_with_reserved_field_raises_at_construction(self) -> None:
        """Raised at Scope() construction, not buried inside check_grant()'s own
        "never raises" contract (which would silently absorb it into a
        generic deny result -- the wrong signal for a caller mistake)."""
        with pytest.raises(ValueError, match="agent_id"):
            Scope(extra={"agent_id": "override"})


class TestRealResponses:
    async def test_allow_decision(self) -> None:
        client = _client(
            _json_handler(200, {"decision": "allow", "reason": None, "approval_context": None})
        )
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "allow"
        assert result.reason is None
        assert result.approval_context is None

    async def test_deny_decision_with_reason(self) -> None:
        client = _client(
            _json_handler(
                200, {"decision": "deny", "reason": "No matching grant", "approval_context": None}
            )
        )
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "deny"
        assert result.reason == "No matching grant"

    async def test_require_approval_carries_approval_context_through_unchanged(self) -> None:
        ctx = {"policy_name": "sensitive-action", "reason": "needs sign-off", "approvers": ["ops"]}
        client = _client(
            _json_handler(
                200,
                {
                    "decision": "require_approval",
                    "reason": "needs sign-off",
                    "approval_context": ctx,
                },
            )
        )
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "require_approval"
        assert result.approval_context == ctx


class TestNeverRaisesFailClosed:
    """The Protocol's own single hard requirement: check_grant must NEVER
    raise for a Presidium-unreachable condition -- always a plain
    GrantResult(decision="deny", ...) return value instead."""

    async def test_connection_error_denies_not_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _client(handler)
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "deny"
        assert "unreachable" in (result.reason or "").lower()

    async def test_timeout_denies_not_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        client = _client(handler)
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "deny"

    async def test_http_500_denies_not_raises(self) -> None:
        client = _client(_json_handler(500, {"error": "internal server error"}))
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "deny"

    async def test_http_401_denies_not_raises(self) -> None:
        """A real scenario: an mTLS misconfiguration on the client side --
        still must never raise up through execute_in_sandbox."""
        client = _client(_json_handler(401, {"error": "client certificate required"}))
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "deny"

    async def test_non_json_body_denies_not_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json at all")

        client = _client(handler)
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "deny"

    async def test_missing_decision_field_denies_not_raises(self) -> None:
        client = _client(_json_handler(200, {"reason": "oops, no decision field"}))
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "deny"
        assert "unrecognized" in (result.reason or "").lower()

    async def test_unrecognized_decision_value_denies_not_raises(self) -> None:
        """A real, adversarial-or-buggy-server scenario: something other
        than the three real Presidium decision values."""
        client = _client(_json_handler(200, {"decision": "maybe"}))
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "deny"

    async def test_json_body_that_is_not_a_dict_denies_not_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["not", "a", "dict"])

        client = _client(handler)
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "deny"


class TestCircuitBreakerIntegration:
    async def test_repeated_failures_trip_the_breaker_and_stop_hitting_the_network(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("down")

        client = _client(handler, failure_threshold=3, cooldown_seconds=1000.0)

        for _ in range(3):
            result = await client.check_grant(agent_id="a", action="x", scope=Scope())
            assert result.decision == "deny"
        assert call_count == 3

        # Breaker is now open -- a 4th call must NOT reach the network at all.
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "deny"
        assert "circuit breaker" in (result.reason or "").lower()
        assert call_count == 3  # unchanged -- short-circuited before the transport

    async def test_a_success_after_failures_resets_the_breaker(self) -> None:
        responses = [httpx.ConnectError("down"), httpx.ConnectError("down")]

        def handler(request: httpx.Request) -> httpx.Response:
            if responses:
                raise responses.pop(0)
            return httpx.Response(200, json={"decision": "allow"})

        client = _client(handler, failure_threshold=5, cooldown_seconds=1000.0)

        await client.check_grant(agent_id="a", action="x", scope=Scope())
        await client.check_grant(agent_id="a", action="x", scope=Scope())
        result = await client.check_grant(agent_id="a", action="x", scope=Scope())
        assert result.decision == "allow"
        assert client._breaker.state == "closed"


class TestClose:
    async def test_close_closes_the_underlying_httpx_client(self) -> None:
        client = _client(_json_handler(200, {"decision": "allow"}))
        await client.close()
        assert client._client.is_closed
