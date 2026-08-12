"""Errors for execute_in_sandbox, ToolManager, SkillManager -- see
docs/contracts/managers.md.
"""

from __future__ import annotations

from typing import Any


class GrantDeniedError(Exception):
    """Raised by execute_in_sandbox after check_grant explicitly returned
    deny. Distinct from check_grant's own contract (which never raises) --
    by the time this is raised, the check has already happened
    deterministically; there's no ambiguity to accidentally swallow.
    """

    def __init__(self, reason: str | None) -> None:
        self.reason = reason
        super().__init__(reason or "grant denied")


class ApprovalRequiredError(Exception):
    """Raised when check_grant returns require_approval. Carries
    approval_context through for the caller to hand to Civitas's
    durable-suspension mechanism -- implementing that mechanism is out of
    scope here.
    """

    def __init__(self, approval_context: dict[str, Any] | None) -> None:
        self.approval_context = approval_context
        super().__init__("approval required")


class SkillParseError(Exception):
    """Raised by SkillManager.load() on a malformed SKILL.md -- missing
    required frontmatter fields, name charset violation, or description
    over 1024 chars.
    """


class SkillNotFoundError(Exception):
    """Raised by SkillManager.run() when name isn't a registered skill."""
