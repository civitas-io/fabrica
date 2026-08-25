"""Tests for InProcessPresidiumClient -- a REAL GovernedRuntime (real
InMemoryRegistry, real CelPolicyEngine), no network, no mocking. This is
the whole point of the in-process client: there's nothing to mock.
"""

from __future__ import annotations

import pytest

pytest.importorskip("presidium")

from presidium.model import (  # noqa: E402
    AgentRecord,
    EvaluationStage,
    Grant,
    PolicyDecision,
    PolicyRule,
)
from presidium.policy.cel import CelPolicyEngine  # noqa: E402
from presidium.registry.memory import InMemoryRegistry  # noqa: E402
from presidium.runtime import GovernedRuntime  # noqa: E402

from fabrica.presidium.in_process_client import InProcessPresidiumClient  # noqa: E402
from fabrica.scope import Scope  # noqa: E402

ALLOW_MATCHING_GRANT = PolicyRule(
    name="allow-matching-grant",
    stage=EvaluationStage.PRE_TOOL,
    expression="""
        agent.grants.exists(g,
            request.resource in g.resources &&
            request.action in g.actions
        )
    """,
    decision=PolicyDecision.ALLOW,
    priority=100,
)
DENY_NO_GRANT = PolicyRule(
    name="deny-no-grant",
    stage=EvaluationStage.PRE_TOOL,
    expression="true",
    decision=PolicyDecision.DENY,
    reason="No matching grant",
    priority=0,
)
ALLOWLISTED_HOST_ONLY = PolicyRule(
    name="allowlisted-host-only",
    stage=EvaluationStage.PRE_TOOL,
    expression="""
        request.resource == "recon:scan" &&
        !(request.parameters.target_host in ["example.com", "api.example.com"])
    """,
    decision=PolicyDecision.DENY,
    reason="Target host not in declared scope",
    priority=200,
)
REQUIRE_APPROVAL_FOR_EXPLOIT = PolicyRule(
    name="exploit-requires-approval",
    stage=EvaluationStage.PRE_TOOL,
    expression='request.resource == "exploit:confirm"',
    decision=PolicyDecision.REQUIRE_APPROVAL,
    reason="Exploitation-tier actions require independent verification",
    approvers=["verification-agent"],
    priority=150,
)


@pytest.fixture
async def runtime() -> GovernedRuntime:
    registry = InMemoryRegistry()
    await registry.register(
        AgentRecord(
            agent_id="presidium://kordon/agent-1",
            name="agent-1",
            public_key="not-a-real-key",
            grants=[Grant(resources=["recon:scan"], actions=["invoke"])],
        )
    )
    engine = CelPolicyEngine()
    engine.load_policies(
        [ALLOW_MATCHING_GRANT, DENY_NO_GRANT, ALLOWLISTED_HOST_ONLY, REQUIRE_APPROVAL_FOR_EXPLOIT]
    )
    return GovernedRuntime(registry=registry, engine=engine)


async def test_allows_a_granted_resource_within_scope(runtime: GovernedRuntime) -> None:
    client = InProcessPresidiumClient(runtime)

    result = await client.check_grant(
        agent_id="presidium://kordon/agent-1",
        action="recon:scan",
        scope=Scope(extra={"target_host": "example.com"}),
    )

    assert result.decision == "allow"


async def test_denies_a_target_host_outside_scope(runtime: GovernedRuntime) -> None:
    """The real proof this closes the loop: a scope-document-derived
    target_host constraint, carried entirely through Scope.extra, reaches
    a real CEL rule and actually blocks the request -- axis 4 of Kordon's
    capability model, made real end to end with no network involved."""
    client = InProcessPresidiumClient(runtime)

    result = await client.check_grant(
        agent_id="presidium://kordon/agent-1",
        action="recon:scan",
        scope=Scope(extra={"target_host": "not-in-scope.example"}),
    )

    assert result.decision == "deny"
    assert result.reason == "Target host not in declared scope"


async def test_denies_an_ungranted_resource(runtime: GovernedRuntime) -> None:
    client = InProcessPresidiumClient(runtime)

    result = await client.check_grant(
        agent_id="presidium://kordon/agent-1", action="exploit:full-takeover", scope=Scope()
    )

    assert result.decision == "deny"
    assert result.reason == "No matching grant"


async def test_require_approval_surfaces_approvers_in_approval_context(
    runtime: GovernedRuntime,
) -> None:
    client = InProcessPresidiumClient(runtime)

    result = await client.check_grant(
        agent_id="presidium://kordon/agent-1", action="exploit:confirm", scope=Scope()
    )

    assert result.decision == "require_approval"
    assert result.approval_context is not None
    assert result.approval_context["approvers"] == ["verification-agent"]


async def test_unknown_agent_id_denies_without_raising(runtime: GovernedRuntime) -> None:
    client = InProcessPresidiumClient(runtime)

    result = await client.check_grant(
        agent_id="presidium://kordon/no-such-agent", action="recon:scan", scope=Scope()
    )

    assert result.decision == "deny"
    assert result.reason == "Agent not found in registry"


async def test_scope_fixed_fields_and_extra_both_reach_the_policy(
    runtime: GovernedRuntime,
) -> None:
    """Confirms _scope_to_parameters flattens BOTH the four fixed Scope
    fields and `extra` into the same request.parameters namespace, not
    two separate structures -- matching RestPresidiumClient's own wire
    behavior exactly."""
    session_rule = PolicyRule(
        name="session-gated",
        stage=EvaluationStage.PRE_TOOL,
        expression='request.resource == "recon:scan" && request.parameters.session_id == "s-1"',
        decision=PolicyDecision.ALLOW,
        priority=300,
    )
    runtime.engine.load_policies(
        [session_rule, ALLOW_MATCHING_GRANT, DENY_NO_GRANT, ALLOWLISTED_HOST_ONLY]
    )
    client = InProcessPresidiumClient(runtime)

    result = await client.check_grant(
        agent_id="presidium://kordon/agent-1",
        action="recon:scan",
        scope=Scope(session_id="s-1", extra={"target_host": "example.com"}),
    )

    assert result.decision == "allow"
