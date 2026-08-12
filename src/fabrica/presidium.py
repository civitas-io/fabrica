"""PresidiumClient -- see docs/contracts/managers.md.

The real REST+mTLS implementation isn't built here -- it needs an actual
Presidium deployment to test against, and belongs naturally near
CivitasBridge's own implementation phase (contracts/civitas-bridge.md).
This module defines the Protocol every ToolManager/SkillManager depends on,
so those can be built and tested now against a fake/test-double
implementation, not blocked on Presidium existing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from fabrica.scope import Scope


@dataclass(frozen=True)
class GrantResult:
    decision: Literal["allow", "deny", "require_approval"]
    reason: str | None = None
    approval_context: dict[str, Any] | None = None
    """Opaque payload passed through to Civitas's durable-suspension
    mechanism when decision == "require_approval". Not interpreted here --
    HITL suspend/resume is a Civitas/Presidium primitive out of scope.
    """


@runtime_checkable
class PresidiumClient(Protocol):
    async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
        """CRITICAL: never raises for a Presidium-unreachable condition.
        Returns GrantResult(decision="deny") instead -- fail-closed must be
        a plain return value the caller is forced to check, not an
        exception a broad `except:` somewhere upstream could accidentally
        swallow and treat as permissive.
        """
        ...
