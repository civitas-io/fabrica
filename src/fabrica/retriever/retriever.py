"""Retriever -- the public engine. See docs/contracts/retriever.md.

Every discovery surface (find(), skill discovery) depends on this class, not
on any specific backend.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

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
    ) -> None:
        """`fallback` defaults to a fresh KeywordBackend if not supplied.
        There is no "no fallback" mode -- every Retriever has one, matching
        the resilience pattern in system-design.md §6.
        """
        self._primary = primary
        self._fallback = fallback if fallback is not None else KeywordBackend()
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

        Dual-write semantics (contracts/retriever.md's open item 2 leaves
        atomicity undecided -- this is a concrete, documented choice, not
        a silent default): primary is attempted first; if it fails,
        fallback becomes the effective store for these items until primary
        recovers, matching search()'s own automatic-fallback behavior. Only
        raises RetrieverUnavailableError if BOTH writes fail.
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
        """
        # Best-effort on both backends -- deregister has no "unavailable"
        # failure mode in the contract; a backend that can't remove an item
        # it may not even have is not a caller-visible error.
        try:
            await self._primary.remove(ids)
        except Exception:
            logger.warning("Retriever: primary backend remove() failed", exc_info=True)
        try:
            await self._fallback.remove(ids)
        except Exception:
            logger.warning("Retriever: fallback backend remove() failed", exc_info=True)

        for id_ in ids:
            self._registered.pop(id_, None)

    async def search(
        self,
        query: str,
        *,
        kind: Literal["tool", "skill"] | None = None,
        limit: int = 5,
        timeout: float = 2.0,
    ) -> list[RankedMatch]:
        """Search the index. Returns at most `limit` matches, pre-sorted
        ascending by rank (rank=0 best).

        On primary-backend failure or timeout, falls back to the configured
        fallback backend automatically -- transparent to the caller.

        Raises:
            RetrieverUnavailableError: both primary and fallback failed or
                timed out.
        """
        try:
            return await asyncio.wait_for(
                self._primary.query(query, kind, limit), timeout=timeout
            )
        except Exception:
            logger.warning(
                "Retriever: primary backend search failed or timed out, falling back",
                exc_info=True,
            )

        try:
            return await asyncio.wait_for(
                self._fallback.query(query, kind, limit), timeout=timeout
            )
        except Exception as exc:
            raise RetrieverUnavailableError(
                "search() failed: both primary and fallback backends errored or timed out"
            ) from exc

    async def list_eager(
        self, kind: Literal["tool", "skill"] | None = None
    ) -> list[Indexable]:
        """Return all Indexables registered with eager=True. Served from
        Retriever's own in-memory cache, not delegated to either backend --
        eager items bypass search by design, not as a performance shortcut.
        """
        return [
            item
            for item in self._registered.values()
            if item.eager and (kind is None or item.kind == kind)
        ]
