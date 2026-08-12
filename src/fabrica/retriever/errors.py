"""Errors for RetrieverBackend and Retriever -- see docs/contracts/retriever.md."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fabrica.retriever.types import Indexable


class RetrieverError(Exception):
    """Base for all Retriever errors."""


class DuplicateIndexableError(RetrieverError):
    """Raised by register() when an id already exists with DIFFERENT
    kind/name/description than the incoming Indexable. Re-registering an
    id with IDENTICAL fields is idempotent and does not raise this -- a
    service restart re-registering its own tool set must not error.
    """

    def __init__(self, id: str, existing: Indexable, incoming: Indexable) -> None:
        self.id = id
        self.existing = existing
        self.incoming = incoming
        super().__init__(
            f"Indexable id={id!r} already registered with different content: "
            f"existing={existing!r} incoming={incoming!r}"
        )


class RetrieverUnavailableError(RetrieverError):
    """Raised only when EVERY backend -- including the fallback -- has
    failed. This should be rare: most individual backend failures are
    absorbed by automatic fallback (system-design.md §6) and never reach
    a caller. If this is raised, both the primary and the zero-dependency
    KeywordBackend fallback are down, which is a serious operational
    signal, not a routine error to catch-and-continue on.
    """
