"""RestPresidiumClient -- the real REST+mTLS PresidiumClient implementation
(docs/contracts/managers.md, docs/system-design.md §1/§6).

Built against civitas-io/presidium's own real, shipped M7 server
(`presidium_contrib.server.build_check_grant_gateway_config`) -- the exact
`POST /v1/check_grant` contract this client speaks is confirmed directly
against that repo's own `PresidiumGatewayAgent.handle_call()` source, not
guessed:

    {"agent_id": "...", "action": "..."} ->
    {"decision": "allow"|"deny"|"require_approval", "reason": ..., "approval_context": ...}

Requires the `fabrica[presidium]` extra (`httpx`) -- deliberately NOT a
core dependency, matching this codebase's own stated architecture
principle that Presidium integration stays duck-typed (`PresidiumClient`
is a Protocol; a caller who wants a different transport, or no Presidium
at all, never needs httpx installed).

**Real, honest gap, closed 2026-08-24 -- this comment was stale until
2026-08-25's own audit caught it**: Presidium's own real server now DOES
thread `scope` through to its CEL policy layer (`FR-1.4`) --
`check_grant()`'s HTTP endpoint deserializes the whole `scope` JSON object
straight into `ActionRequest.parameters`, so a CEL policy can reference
`request.parameters.<key>` for anything sent here. `Scope`'s own `extra`
field (added 2026-08-25, same audit) is the client-side half of this --
see `fabrica.scope.Scope`'s own docstring.
"""

from __future__ import annotations

import dataclasses
import enum
import ssl
import time
from typing import Any

import httpx

from fabrica.presidium import GrantResult
from fabrica.scope import Scope

_DEFAULT_TIMEOUT_SECONDS = 5.0
_DEFAULT_FAILURE_THRESHOLD = 5
_DEFAULT_COOLDOWN_SECONDS = 30.0

_VALID_DECISIONS = frozenset({"allow", "deny", "require_approval"})


class _BreakerState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _CircuitBreaker:
    """A minimal, real circuit breaker -- system-design.md §6's own spec,
    verbatim: "after N consecutive failures, trips open and returns deny
    immediately (no fresh timeout wait per call) until a cooldown elapses,
    then half-opens to test recovery."

    Three states: CLOSED (normal -- every call attempted), OPEN (tripped
    -- every call short-circuited without even touching the network,
    until the cooldown elapses), HALF_OPEN (cooldown elapsed -- exactly
    one real call is let through to test recovery; success closes the
    breaker, failure reopens it with a fresh cooldown window).

    ``_now`` is injectable for deterministic tests -- the same pattern
    ``presidium.trust.LinearTrustScore`` uses (a sibling project in this
    same org), not invented here.
    """

    def __init__(
        self,
        *,
        failure_threshold: int,
        cooldown_seconds: float,
        _now: Any = None,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._now = _now if _now is not None else time.monotonic
        self._state = _BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    def allow_request(self) -> bool:
        """True if a real request should be attempted. False means the
        breaker is open and the cooldown hasn't elapsed -- the caller
        must short-circuit to a deny without touching the network.

        Transitions OPEN -> HALF_OPEN here (not in a separate step) --
        the "one real call" HALF_OPEN allows is exactly the call that
        immediately follows this returning True after the cooldown.
        """
        if self._state is _BreakerState.OPEN:
            assert self._opened_at is not None
            if self._now() - self._opened_at >= self._cooldown_seconds:
                self._state = _BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = _BreakerState.CLOSED
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._state is _BreakerState.HALF_OPEN or (
            self._consecutive_failures >= self._failure_threshold
        ):
            self._state = _BreakerState.OPEN
            self._opened_at = self._now()

    @property
    def state(self) -> str:
        """Read-only, for tests/observability -- never used for control flow."""
        return self._state.value


def _scope_to_dict(scope: Scope) -> dict[str, str]:
    """Only non-None fixed fields -- an absent field and an explicit null carry
    different meaning to a policy engine (e.g. a CEL expression testing
    `has(request.parameters.team_id)`); omitting is more honest than sending
    nulls for fields the caller never set. ``scope.extra`` is merged in flat
    (not nested under an ``"extra"`` key) -- see ``Scope.extra``'s own
    docstring for why. ``Scope.__post_init__`` already guarantees ``extra``
    can never collide with a reserved fixed-field name by the time an
    instance exists, so no re-validation here.
    """
    payload = {k: v for k, v in dataclasses.asdict(scope).items() if k != "extra" and v is not None}
    payload.update(scope.extra)
    return payload


class RestPresidiumClient:
    """The real ``PresidiumClient`` implementation: REST + mTLS, circuit-
    breaker protected, fail-closed on any unreachable/malformed condition
    -- never raises, matching the Protocol's own contract exactly.

    Construct via :meth:`from_endpoint` for the common case (mTLS cert
    paths on disk); the bare constructor takes an already-configured
    ``httpx.AsyncClient`` directly, for dependency injection in tests or
    a caller with an unusual transport setup (a custom proxy, a
    pre-warmed connection pool, etc.) -- matching this codebase's own
    "external dependencies are always fully-constructed objects" rule
    (docs/contracts/civitas-bridge.md).
    """

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._breaker = _CircuitBreaker(
            failure_threshold=failure_threshold, cooldown_seconds=cooldown_seconds
        )

    @classmethod
    def from_endpoint(
        cls,
        base_url: str,
        *,
        client_cert: str | None = None,
        client_key: str | None = None,
        ca_cert: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
    ) -> RestPresidiumClient:
        """The real mTLS convenience factory (docs/contracts/
        civitas-bridge.md: "Convenience factories... belong on the
        dependency's own class").

        Builds one fully-configured ``ssl.SSLContext`` (``load_verify_
        locations`` + ``load_cert_chain``), passed to httpx as
        ``verify=<context>`` -- deliberately NOT httpx's ``cert=(cert,
        key)`` + ``verify=<str path>`` combination, which has a real,
        confirmed bug/incompatibility in current httpx (found the hard
        way earlier this session, civitas-io/python-civitas's own
        docs/design/gateway-http-mtls-direct.md §9: a fully valid,
        correctly-loaded client cert produced a bare, signal-free
        ``httpx.ReadError`` with that legacy API, and worked immediately
        once switched to this one).

        ``client_cert``/``client_key``/``ca_cert`` are all optional --
        omit all three for a plaintext or server-cert-only (no client
        mTLS) deployment; a real Presidium deployment with
        ``client_cert_mode="required"`` needs all three.
        """
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if ca_cert is not None:
            ssl_context.load_verify_locations(ca_cert)
        if client_cert is not None and client_key is not None:
            ssl_context.load_cert_chain(client_cert, client_key)
        client = httpx.AsyncClient(verify=ssl_context, timeout=timeout)
        return cls(
            base_url=base_url,
            client=client,
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )

    async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
        """Never raises -- every real failure mode (breaker open, network
        error, timeout, non-2xx, malformed/missing JSON, an unrecognized
        ``decision`` value) returns ``GrantResult(decision="deny", ...)``
        instead, exactly matching the Protocol's own documented contract.
        """
        if not self._breaker.allow_request():
            return GrantResult(
                decision="deny",
                reason="Presidium unreachable (circuit breaker open)",
            )

        try:
            response = await self._client.post(
                f"{self._base_url}/v1/check_grant",
                json={"agent_id": agent_id, "action": action, "scope": _scope_to_dict(scope)},
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # httpx.HTTPError covers connection errors, timeouts, and
            # raise_for_status()'s non-2xx; ValueError covers response.json()
            # on a non-JSON body -- both are "Presidium didn't answer
            # correctly," the same failure class the circuit breaker exists
            # to track.
            self._breaker.record_failure()
            return GrantResult(decision="deny", reason=f"Presidium unreachable: {exc}")

        decision = body.get("decision") if isinstance(body, dict) else None
        if decision not in _VALID_DECISIONS:
            self._breaker.record_failure()
            return GrantResult(
                decision="deny",
                reason=f"Presidium returned an unrecognized response: {body!r}",
            )

        self._breaker.record_success()
        return GrantResult(
            decision=decision,
            reason=body.get("reason"),
            approval_context=body.get("approval_context"),
        )

    async def close(self) -> None:
        """Matches this codebase's own async-resource convention
        (SandboxPool.close(), CivitasBridge.close()) -- not a context
        manager, an explicit call the owner makes at shutdown."""
        await self._client.aclose()
