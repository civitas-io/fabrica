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


class PromptTooLargeError(PromptError):
    """Raised by PromptTemplate's own construction-time validation when
    `content` exceeds MAX_PROMPT_CONTENT_BYTES -- closes contracts/
    prompts.md open item 3. Reject, don't truncate: unlike RunResult
    .stdout (execution OUTPUT, truncation is a reasonable lossy summary),
    a prompt's content is AUTHORED, instructional input -- silently
    truncating it would corrupt its meaning, not just its length. An
    author who hits this should shorten the prompt deliberately, not have
    it silently cut off.
    """


class InvalidCacheBoundaryError(PromptError):
    """Raised by PromptTemplate's own construction-time validation when
    `cache_boundary` is negative or past the end of `content` -- closes
    contracts/prompts.md open item 4. Reject, don't pass through: an
    invalid offset would silently misbehave for whatever downstream
    consumer slices `content` at that boundary (a negative index doesn't
    error in Python, it silently wraps) -- catching the author's mistake
    here, at write time, beats a confusing runtime bug later, in code
    this contract doesn't own.
    """
