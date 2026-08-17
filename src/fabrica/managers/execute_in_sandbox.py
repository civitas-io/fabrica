"""execute_in_sandbox -- the shared orchestration, in exactly one place.
See docs/contracts/managers.md.

Used by both ToolManager.run_code() and SkillManager.run()
(system-design.md §1's composition-over-inheritance resolution -- this
function IS the "composition," not a base class).
"""

from __future__ import annotations

import hashlib
import time

from fabrica.managers.errors import ApprovalRequiredError, GrantDeniedError
from fabrica.observability import NullTracer, Tracer, traced
from fabrica.presidium import PresidiumClient
from fabrica.sandbox import RunResult, SandboxPool, ToolCallCallback
from fabrica.scope import Scope


async def execute_in_sandbox(
    *,
    presidium_client: PresidiumClient,
    sandbox_pool: SandboxPool,
    action: str,
    agent_id: str,
    scope: Scope,
    code: str,
    on_tool_call: ToolCallCallback,
    timeout: float = 30.0,
    tool_call_timeout: float | None = None,
    tracer: Tracer | None = None,
    skill_name: str | None = None,
) -> RunResult:
    """Sequence: check_grant -> acquire -> run -> release (always) -> span.

    Sandbox-level exceptions (SandboxPoolExhaustedError, SandboxTimeoutError,
    SandboxCrashedError) propagate unchanged -- this function does not wrap
    them in a new error type.

    Real span emission (system-design.md §7), not a logger.info stand-in --
    the outer fabrica.tool.code_mode.run / fabrica.skill.run span is the
    real parent of fabrica.presidium.check_grant, fabrica.sandbox.acquire,
    and fabrica.sandbox.run underneath it, nested via trace_id/
    parent_span_id, not four disconnected spans that merely share a name
    prefix. `tracer` defaults to NullTracer() (a real no-op) -- matching
    every other DI'd dependency in this codebase; `ToolManager`/
    `SkillManager` pass through whatever they were constructed with.
    """
    active_tracer = tracer if tracer is not None else NullTracer()
    span_name = "fabrica.tool.code_mode.run" if action == "code_mode" else "fabrica.skill.run"
    code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]

    with traced(
        active_tracer,
        span_name,
        agent_id=agent_id,
        code_hash=code_hash,
        skill_name=skill_name,
        user_id=scope.user_id,
        session_id=scope.session_id,
        team_id=scope.team_id,
    ) as outer_span:
        with traced(
            active_tracer,
            "fabrica.presidium.check_grant",
            trace_id=outer_span.trace_id,
            parent_span_id=outer_span.span_id,
            action=action,
            agent_id=agent_id,
        ) as grant_span:
            grant_check_start = time.monotonic()
            grant = await presidium_client.check_grant(
                agent_id=agent_id, action=action, scope=scope
            )
            grant_span.set_attribute("decision", grant.decision)
            grant_span.set_attribute(
                "latency_ms", round((time.monotonic() - grant_check_start) * 1000, 2)
            )

        if grant.decision == "deny":
            raise GrantDeniedError(grant.reason)
        if grant.decision == "require_approval":
            raise ApprovalRequiredError(grant.approval_context)

        handle = await sandbox_pool.acquire(
            trace_id=outer_span.trace_id, parent_span_id=outer_span.span_id
        )
        try:
            result = await sandbox_pool.run(
                handle,
                code,
                on_tool_call=on_tool_call,
                timeout=timeout,
                tool_call_timeout=tool_call_timeout,
                trace_id=outer_span.trace_id,
                parent_span_id=outer_span.span_id,
            )
        finally:
            await sandbox_pool.release(handle)

        outer_span.set_attribute("duration_ms", result.duration_ms)
        outer_span.set_attribute("tool_call_count", result.tool_call_count)
        return result
