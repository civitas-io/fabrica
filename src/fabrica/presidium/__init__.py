"""PresidiumClient -- see docs/contracts/managers.md.

This package defines the Protocol every ToolManager/SkillManager depends
on (this module), plus the real REST+mTLS implementation
(rest_client.py, `fabrica[presidium]` extra) -- built once a real
Presidium deployment existed to test against (civitas-io/presidium's own
v0.2.1+ server, M7), matching the plan this module originally deferred to.

Kept as a package (not a single presidium.py module) to mirror this
codebase's own established Protocol-plus-concrete-provider(s) shape
(fabrica.tunnel.backend/cloudflare_provider,
fabrica.sandbox.backend/firecracker_backend) -- one Protocol, one or more
real implementations, each importable independently so a plain `pip
install fabrica-context` never requires httpx unless RestPresidiumClient
is actually used.
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
