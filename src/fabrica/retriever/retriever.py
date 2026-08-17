"""Retriever -- the public engine. See docs/contracts/retriever.md.

Every discovery surface (find(), skill discovery) depends on this class, not
on any specific backend.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fabrica.observability import NullTracer, Tracer, traced
from fabrica.retriever.backend import RetrieverBackend
from fabrica.retriever.errors import DuplicateIndexableError, RetrieverUnavailableError
from fabrica.retriever.keyword_backend import KeywordBackend
from fabrica.retriever.types import Indexable, RankedMatch

logger = logging.getLogger(__name__)


class Retriever:
    def __init__(
        self,
        primary: RetrieverBackend,
        fallback: RetrieverBackend | None = None,
        *,
        tracer: Tracer | None = None,
    ) -> None:
        """`fallback` defaults to a fresh KeywordBackend if not supplied.
        There is no "no fallback" mode -- every Retriever has one, matching
        the resilience pattern in system-design.md §6.

        `tracer` emits `fabrica.retriever.search` (system-design.md §7) --
        defaults to `NullTracer()`, a real no-op, matching the
        `NullPresidiumClient`/`NullCompactor` DI pattern. `CivitasBridge`
        is the one place licensed to wire in a real one.
        """
        self._primary = primary
        self._fallback = fallback if fallback is not None else KeywordBackend()
        self._tracer = tracer if tracer is not None else NullTracer()
        # Retriever's own bookkeeping -- NOT delegated to either backend.
        # Needed for duplicate detection (RetrieverBackend has no get(id))
        # and for list_eager()'s cache, per the contract's explicit note
        # that eager items bypass search by design, not as a shortcut.
        self._registered: dict[str, Indexable] = {}

    async def register(self, items: list[Indexable]) -> None:
        """Add or update items in the index -- writes to BOTH the primary
        and fallback backend, so fallback search results stay complete even
        during a primary outage.

        Idempotent for identical re-registration. Raises
        DuplicateIndexableError if an id already exists with different
        content.

        Dual-write semantics: primary is attempted first; if it fails,
        fallback becomes the effective store for these items until primary
        recovers, matching search()'s own automatic-fallback behavior. Only
        raises RetrieverUnavailableError if BOTH writes fail.

        Batch atomicity (contracts/retriever.md open item 2): the
        duplicate-id check below runs over the WHOLE list before either
        backend is touched -- one bad item in a large batch means NOTHING
        in that batch gets written, a real all-or-nothing guarantee at
        this stage. Past that check, each backend's own add() call gets
        the whole list in one call; RetrieverBackend.add()'s '-> None'
        signature has no way to report which items in a large batch
        succeeded if a backend can only fail partway through one --
        decided in favor of best-effort per-item application, matching
        what the only backend that exists today (KeywordBackend) actually
        does (an unconditional loop, incapable of partial failure).
        Revisit only if a future backend can genuinely fail partway
        through a batch AND the Protocol grows a way to report that --
        both would need to change together; there is no way to honor an
        all-or-nothing promise with today's signature.
        """
        for item in items:
            existing = self._registered.get(item.id)
            if existing is not None and (
                existing.kind != item.kind
                or existing.name != item.name
                or existing.description != item.description
            ):
                # Deliberately NOT full dataclass equality: the contract's
                # DuplicateIndexableError docstring names kind/name/
                # description as the identity-bearing fields, not `eager`.
                # Comparing every field would incorrectly reject a
                # legitimate re-registration that only toggles eager --
                # register()'s own docstring calls this "add OR UPDATE".
                raise DuplicateIndexableError(item.id, existing, item)

        primary_ok = True
        try:
            await self._primary.add(items)
        except Exception:
            primary_ok = False
            logger.warning(
                "Retriever: primary backend add() failed, trying fallback", exc_info=True
            )

        fallback_ok = True
        try:
            await self._fallback.add(items)
        except Exception:
            fallback_ok = False
            logger.warning("Retriever: fallback backend add() failed", exc_info=True)

        if not primary_ok and not fallback_ok:
            raise RetrieverUnavailableError(
                "register() failed: both primary and fallback backends errored"
            )

        for item in items:
            self._registered[item.id] = item

    async def deregister(self, ids: list[str]) -> None:
        """Remove items by id. Deregistering an id that doesn't exist is a
        no-op, not an error.

        `list_eager()`'s cache (this class's own `_registered` bookkeeping)
        is invalidated IMMEDIATELY, before either backend removal is
        attempted -- closes contracts/retriever.md's open item 1 (a
        deregistered eager item staying visible to `list_eager()` during
        the await window while backend removal is still in flight).
        `deregister()`'s intent takes effect the instant it's called, not
        once best-effort backend cleanup happens to finish.
        """
        for id_ in ids:
            self._registered.pop(id_, None)

        # Best-effort on both backends -- deregister has no "unavailable"
        # failure mode in the contract; a backend that can't remove an item
        # it may not even have is not a caller-visible error. This can only
        # affect search()'s eventual consistency, never list_eager()'s --
        # that cache is already correct by the time we get here.
        try:
            await self._primary.remove(ids)
        except Exception:
            logger.warning("Retriever: primary backend remove() failed", exc_info=True)
        try:
            await self._fallback.remove(ids)
        except Exception:
            logger.warning("Retriever: fallback backend remove() failed", exc_info=True)

    async def search(
        self,
        query: str,
        *,
        kind: Literal["tool", "skill"] | None = None,
        limit: int = 5,
        timeout: float = 2.0,
        trace_id: str = "",
        parent_span_id: str | None = None,
    ) -> list[RankedMatch]:
        """Search the index. Returns at most `limit` matches, pre-sorted
        ascending by rank (rank=0 best).

        On primary-backend failure or timeout, falls back to the configured
        fallback backend automatically -- transparent to the caller.

        `trace_id`/`parent_span_id` let a caller (e.g. `ToolManager.find()`)
        nest this span under its own -- both default to "start a fresh root
        span", so a direct caller never has to think about tracing to use
        this method correctly.

        Raises:
            RetrieverUnavailableError: both primary and fallback failed or
                timed out.
        """
        with traced(
            self._tracer,
            "fabrica.retriever.search",
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            query=query,
            kind=kind,
            limit=limit,
        ) as span:
            try:
                results = await asyncio.wait_for(
                    self._primary.query(query, kind, limit), timeout=timeout
                )
                span.set_attribute("backend", "primary")
            except Exception:
                logger.warning(
                    "Retriever: primary backend search failed or timed out, falling back",
                    exc_info=True,
                )

                try:
                    results = await asyncio.wait_for(
                        self._fallback.query(query, kind, limit), timeout=timeout
                    )
                    span.set_attribute("backend", "fallback")
                except Exception as exc:
                    raise RetrieverUnavailableError(
                        "search() failed: both primary and fallback backends errored or timed out"
                    ) from exc

            span.set_attribute("top_rank", results[0].rank if results else -1)
            return results

    async def list_eager(self, kind: Literal["tool", "skill"] | None = None) -> list[Indexable]:
        """Return all Indexables registered with eager=True. Served from
        Retriever's own in-memory cache, not delegated to either backend --
        eager items bypass search by design, not as a performance shortcut.
        """
        return [
            item
            for item in self._registered.values()
            if item.eager and (kind is None or item.kind == kind)
        ]
