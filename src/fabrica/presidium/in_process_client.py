"""InProcessPresidiumClient -- the single-node PresidiumClient implementation
(docs/contracts/managers.md, docs/system-design.md §1/§6).

Wraps a real `presidium.GovernedRuntime` directly, in-process -- no network
hop, no mTLS setup. This is the natural counterpart to `RestPresidiumClient`
for exactly the same reason Civitas's own `MessageBus` supports both an
`in_process` and a distributed (`nats`) transport: a single-node deployment
shouldn't have to run a separate Presidium server process, manage
certificates, or pay a loopback network round trip just to evaluate a
policy check against governance state that already lives in the same
process.

Speaks the exact same logical contract `RestPresidiumClient` speaks over
HTTP (`docs/contracts/managers.md`'s `check_grant` semantics), just without
serializing through JSON/HTTP: resolve `agent_id` to a real `AgentRecord`
via the runtime's own registry, call `GovernedToolProvider.check_grant()`
with `scope` flattened into `parameters` exactly like
`presidium_contrib.server.gateway_agent.PresidiumGatewayAgent.handle_call()`
does server-side, translate `PolicyResult` back into `GrantResult`.
"""

from __future__ import annotations

from presidium.model import PolicyDecision
from presidium.runtime import GovernedRuntime

from fabrica.presidium import GrantResult
from fabrica.scope import Scope

_RESOURCE_ACTION = "invoke"
"""Presidium's own generic verb -- resource = the caller's action string
verbatim, matching gateway_agent.py's FR-1.3 convention exactly, so a
policy rule written against the REST path behaves identically here."""

_DECISION_MAP: dict[PolicyDecision, str] = {
    PolicyDecision.ALLOW: "allow",
    PolicyDecision.DENY: "deny",
    PolicyDecision.REQUIRE_APPROVAL: "require_approval",
}


def _scope_to_parameters(scope: Scope) -> dict[str, str]:
    """Same flattening `RestPresidiumClient._scope_to_dict()` performs on
    the wire -- kept identical so a rule written against either transport
    sees the same `request.parameters` shape. Only non-None fixed fields
    plus `extra`'s own keys, flattened (never nested under `"extra"`).
    """
    fixed = {
        k: v
        for k, v in (
            ("user_id", scope.user_id),
            ("session_id", scope.session_id),
            ("agent_id", scope.agent_id),
            ("team_id", scope.team_id),
        )
        if v is not None
    }
    return {**fixed, **scope.extra}


class InProcessPresidiumClient:
    """The real `PresidiumClient` implementation for single-node/desktop
    deployments. Never raises -- every real failure mode (unresolvable
    agent_id, an unrecognized decision somehow coming back from the
    engine) returns `GrantResult(decision="deny")` instead, matching the
    Protocol's own documented contract exactly, same discipline as
    `RestPresidiumClient`.
    """

    def __init__(self, runtime: GovernedRuntime) -> None:
        self._runtime = runtime

    async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
        record = await self._runtime.registry.lookup_by_id(agent_id)
        if record is None:
            return GrantResult(decision="deny", reason="Agent not found in registry")

        result = await self._runtime.tool_provider.check_grant(
            record.name,
            resource=action,
            action=_RESOURCE_ACTION,
            parameters=_scope_to_parameters(scope),
        )

        decision = _DECISION_MAP.get(result.decision)
        if decision is None:
            # Defensive, not reachable with today's PolicyDecision enum --
            # fail-closed the same way an unrecognized wire value does in
            # RestPresidiumClient, in case the engine's enum ever grows a
            # member this mapping hasn't been updated for.
            return GrantResult(
                decision="deny", reason=f"Unrecognized policy decision: {result.decision!r}"
            )

        approval_context: dict[str, object] | None = None
        if result.decision == PolicyDecision.REQUIRE_APPROVAL:
            approval_context = {
                "policy_name": result.policy_name,
                "reason": result.reason,
                "approvers": list(result.approvers) if result.approvers else None,
            }

        return GrantResult(
            decision=decision,  # type: ignore[arg-type]
            reason=result.reason,
            approval_context=approval_context,
        )
