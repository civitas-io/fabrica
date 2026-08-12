"""Errors for the memory facets -- see docs/contracts/memory.md."""

from __future__ import annotations


class MemoryError(Exception):
    """Base for all MemoryManager/MemoryStore/WorkingMemoryStore/Compactor
    errors.
    """


class MemoryBackendError(MemoryError):
    """The configured long-term adapter failed. Wraps whatever the
    underlying library raised so callers depend on ONE error type
    regardless of which adapter is configured.
    """

    def __init__(self, backend: str, cause: Exception) -> None:
        self.backend = backend
        self.cause = cause
        super().__init__(f"{backend} backend failed: {cause}")


class WorkingMemoryQuotaExceeded(MemoryError):
    """remember() would exceed the per-scope size ceiling. Raised, not
    silently accepted or truncated -- key-value data has no natural
    truncation point.
    """


class CompactionError(MemoryError):
    """The injected Summarizer raised during compact(). Not swallowed or
    silently downgraded to naive truncation.
    """

    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(f"compaction failed: {cause}")


class CompactionUnavailableError(MemoryError):
    """Raised by NullCompactor.compact() -- compaction was invoked, but no
    Summarizer was ever configured.
    """
