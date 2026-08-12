"""execute_in_sandbox -- the shared orchestration, in exactly one place.
See docs/contracts/managers.md.

Used by both ToolManager.run_code() and SkillManager.run()
(system-design.md §1's composition-over-inheritance resolution -- this
function IS the "composition," not a base class).
"""

from __future__ import annotations

import logging

from fabrica.managers.errors import ApprovalRequiredError, GrantDeniedError
from fabrica.presidium import PresidiumClient
from fabrica.sandbox import RunResult, SandboxPool, ToolCallCallback
from fabrica.scope import Scope

logger = logging.getLogger(__name__)


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
) -> RunResult:
    """Sequence: check_grant -> acquire -> run -> release (always) -> span.

    Sandbox-level exceptions (SandboxPoolExhaustedError, SandboxTimeoutError,
    SandboxCrashedError) propagate unchanged -- this function does not wrap
    them in a new error type.
    """
    grant = await presidium_client.check_grant(agent_id=agent_id, action=action, scope=scope)
    if grant.decision == "deny":
        raise GrantDeniedError(grant.reason)
    if grant.decision == "require_approval":
        raise ApprovalRequiredError(grant.approval_context)

    handle = await sandbox_pool.acquire()
    try:
        result = await sandbox_pool.run(handle, code, on_tool_call=on_tool_call, timeout=timeout)
    finally:
        await sandbox_pool.release(handle)

    _emit_span(action=action, agent_id=agent_id, scope=scope)
    return result


def _emit_span(*, action: str, agent_id: str, scope: Scope) -> None:
    """system-design.md §7's span table -- fabrica.tool.code_mode.run /
    fabrica.skill.run, with Scope fields as attributes. This is a minimal
    stand-in (structured log, not a real OTEL exporter) -- wiring an actual
    OTEL SDK is a CivitasBridge-level concern (Fabrica emits, Civitas
    collects, per civitas-presidium-integration.md), not something this
    function should own end to end before that integration exists.
    """
    span_name = "fabrica.tool.code_mode.run" if action == "code_mode" else "fabrica.skill.run"
    logger.info(
        "%s agent_id=%s user_id=%s session_id=%s team_id=%s",
        span_name,
        agent_id,
        scope.user_id,
        scope.session_id,
        scope.team_id,
    )
