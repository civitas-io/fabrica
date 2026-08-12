"""MemoryStore (long-term) -- see docs/contracts/memory.md.

The real fabrica-contrib[mem0|zep|letta|cognee|langmem] adapters aren't
built here (memory.md's "wrap, don't build" thesis -- each needs a real
external service/library to wrap and test against). InMemoryMemoryStore is
a genuine, working zero-infra default -- not a stub -- reusing the same
rank-bm25 scoring approach as fabrica.retriever.keyword_backend, since
memory search has the identical "rank free-text against a description-like
field" shape, just scoped per-Scope instead of shared.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from rank_bm25 import BM25Okapi

from fabrica.memory.errors import MemoryBackendError
from fabrica.memory.types import MemoryItem
from fabrica.scope import Scope

_ScopeKey = tuple[str | None, str | None, str | None, str | None]

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@runtime_checkable
class MemoryStore(Protocol):
    async def write(self, scope: Scope, item: MemoryItem) -> str: ...
    async def search(self, scope: Scope, query: str, limit: int = 5) -> list[MemoryItem]: ...
    async def get(self, scope: Scope, id: str) -> MemoryItem | None: ...
    async def forget(self, scope: Scope, id: str) -> None: ...


class InMemoryMemoryStore:
    """Zero-infra default. Scoped by the full Scope tuple -- memory is
    inherently scoped, unlike Retriever's shared registry.
    """

    def __init__(self) -> None:
        self._items: dict[_ScopeKey, dict[str, MemoryItem]] = {}

    def _scope_key(self, scope: Scope) -> _ScopeKey:
        return (scope.user_id, scope.session_id, scope.agent_id, scope.team_id)

    async def write(self, scope: Scope, item: MemoryItem) -> str:
        try:
            new_id = item.id or str(uuid.uuid4())
            stored = MemoryItem(
                id=new_id,
                content=item.content,
                metadata=item.metadata,
                created_at=item.created_at or datetime.now(UTC),
                score=None,
            )
            self._items.setdefault(self._scope_key(scope), {})[new_id] = stored
            return new_id
        except Exception as exc:
            raise MemoryBackendError("in_memory", exc) from exc

    async def search(self, scope: Scope, query: str, limit: int = 5) -> list[MemoryItem]:
        try:
            candidates = list(self._items.get(self._scope_key(scope), {}).values())
            if not candidates:
                return []
            corpus = [_tokenize(item.content) for item in candidates]
            bm25 = BM25Okapi(corpus)
            scores = bm25.get_scores(_tokenize(query))
            ranked = sorted(zip(candidates, scores, strict=True), key=lambda p: p[1], reverse=True)
            return [
                MemoryItem(
                    id=item.id,
                    content=item.content,
                    metadata=item.metadata,
                    created_at=item.created_at,
                    score=float(score),
                )
                for item, score in ranked[:limit]
            ]
        except Exception as exc:
            raise MemoryBackendError("in_memory", exc) from exc

    async def get(self, scope: Scope, id: str) -> MemoryItem | None:
        return self._items.get(self._scope_key(scope), {}).get(id)

    async def forget(self, scope: Scope, id: str) -> None:
        self._items.get(self._scope_key(scope), {}).pop(id, None)
