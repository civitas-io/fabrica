"""Errors for PromptStore/PromptManager -- see docs/contracts/prompts.md."""

from __future__ import annotations


class PromptError(Exception):
    """Base for all PromptStore/PromptManager errors."""


class PromptBackendError(PromptError):
    """The configured backend failed. Same wrapping guarantee as
    MemoryBackendError: callers depend on one error type regardless of
    backend.
    """

    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(f"prompt backend failed: {cause}")


class PromptParseError(PromptError):
    """Raised by PromptManager.load() on a malformed PROMPT.md. Mirrors
    SkillParseError's role for SKILL.md -- same shape, different file
    format.
    """
