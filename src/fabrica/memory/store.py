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
from typing import Any, Protocol, runtime_checkable

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


@runtime_checkable
class BlobStore(Protocol):
    """The minimal shape PersistedMemoryStore needs from a backing store --
    a single whole-dict snapshot, no partial updates. Deliberately NOT an
    import of fabrica.civitas_bridge.state.ComponentStateHandle -- this
    module depends on a shape, not a package, same pattern as
    PresidiumClient/Summarizer/CivitasRuntime. Also structurally necessary,
    not just stylistic: fabrica.civitas_bridge already imports fabrica.memory
    (for MemoryManager), so fabrica.memory importing anything from
    fabrica.civitas_bridge back would be a real circular import, not just
    an architectural preference. ComponentStateHandle already satisfies
    this shape today -- CivitasBridge.build() passes one in without needing
    this module to know that type exists.
    """

    async def get(self) -> dict[str, Any] | None: ...
    async def set(self, state: dict[str, Any]) -> None: ...


class PersistedMemoryStore:
    """MemoryStore backed by a BlobStore (in practice, a Civitas
    ComponentStateHandle) -- write-through, not lazy: every write()/forget()
    persists the ENTIRE current snapshot immediately, since BlobStore has
    no partial-update operation to persist just one changed item. Reads
    (search()/get()) never touch the BlobStore -- the full state is loaded
    ONCE, at construction (via the create() factory), and kept in memory
    for the process's lifetime; this is a real, deliberate trade (fast
    reads, no per-read I/O) not a shortcut -- correct because
    ComponentStateHandle's contract has exactly one writer (the
    process/component that owns this component_name), never a fanned-out
    read from elsewhere.

    Reuses InMemoryMemoryStore's exact matching/scoring logic internally
    (composition, not inheritance -- an InMemoryMemoryStore's live _items
    dict is not directly reusable, since this class must intercept every
    mutation to persist, but the reuse means write()/search()/get()/
    forget()'s actual semantics can never silently drift between the two
    implementations).
    """

    def __init__(self, blob_store: BlobStore, delegate: InMemoryMemoryStore) -> None:
        """Not the public construction path -- use create(), which loads
        any existing snapshot before this object is usable.
        """
        self._blob_store = blob_store
        self._delegate = delegate

    @classmethod
    async def create(cls, blob_store: BlobStore) -> PersistedMemoryStore:
        delegate = InMemoryMemoryStore()
        blob = await blob_store.get()
        if blob is not None:
            _restore_memory_snapshot(delegate, blob)
        return cls(blob_store, delegate)

    async def _persist(self) -> None:
        await self._blob_store.set(_memory_snapshot(self._delegate))

    async def write(self, scope: Scope, item: MemoryItem) -> str:
        new_id = await self._delegate.write(scope, item)
        await self._persist()
        return new_id

    async def search(self, scope: Scope, query: str, limit: int = 5) -> list[MemoryItem]:
        return await self._delegate.search(scope, query, limit)

    async def get(self, scope: Scope, id: str) -> MemoryItem | None:
        return await self._delegate.get(scope, id)

    async def forget(self, scope: Scope, id: str) -> None:
        await self._delegate.forget(scope, id)
        await self._persist()


def _memory_snapshot(store: InMemoryMemoryStore) -> dict[str, Any]:
    """JSON-serializable snapshot of an InMemoryMemoryStore's full state.
    Scope keys are 4-tuples (JSON has no tuple type) -- serialized as a
    4-element list per record instead of as a dict key, sidestepping the
    "tuple isn't a valid JSON object key" problem entirely rather than
    encoding it into a string key that would need its own parsing rules.
    """
    records = []
    for scope_key, items in store._items.items():
        for item in items.values():
            records.append(
                {
                    "scope_key": list(scope_key),
                    "id": item.id,
                    "content": item.content,
                    "metadata": item.metadata,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
            )
    return {"records": records}


def _restore_memory_snapshot(store: InMemoryMemoryStore, blob: dict[str, Any]) -> None:
    for record in blob.get("records", []):
        scope_key = tuple(record["scope_key"])
        created_at = datetime.fromisoformat(record["created_at"]) if record["created_at"] else None
        item = MemoryItem(
            id=record["id"],
            content=record["content"],
            metadata=record["metadata"],
            created_at=created_at,
            score=None,
        )
        store._items.setdefault(scope_key, {})[item.id] = item  # type: ignore[index]
