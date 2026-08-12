"""PromptStore -- see docs/contracts/prompts.md."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from fabrica.prompts.errors import PromptBackendError
from fabrica.prompts.types import PromptTemplate


@runtime_checkable
class PromptStore(Protocol):
    async def put(
        self,
        name: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        cacheable: bool = False,
        cache_boundary: int | None = None,
    ) -> PromptTemplate: ...

    async def get(self, name: str, version: int | None = None) -> PromptTemplate | None: ...

    async def list_versions(self, name: str) -> list[int]: ...

    async def list_names(self) -> list[str]: ...

    async def delete(self, name: str, version: int | None = None) -> None: ...


class InMemoryPromptStore:
    """Default: in-process, zero infra. Per open item 2, concurrent put()
    calls to the same name need atomic version assignment -- resolved here
    with an asyncio.Lock; other PromptStore implementations must provide
    an equivalent guarantee themselves, since the Protocol doesn't
    prescribe HOW.
    """

    def __init__(self) -> None:
        self._templates: dict[str, dict[int, PromptTemplate]] = {}
        self._lock = asyncio.Lock()

    async def put(
        self,
        name: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        cacheable: bool = False,
        cache_boundary: int | None = None,
    ) -> PromptTemplate:
        try:
            async with self._lock:
                versions = self._templates.setdefault(name, {})
                next_version = max(versions.keys(), default=0) + 1
                template = PromptTemplate(
                    name=name,
                    version=next_version,
                    content=content,
                    metadata=metadata or {},
                    created_at=datetime.now(UTC),
                    cacheable=cacheable,
                    cache_boundary=cache_boundary,
                )
                versions[next_version] = template
                return template
        except Exception as exc:
            raise PromptBackendError(exc) from exc

    async def get(self, name: str, version: int | None = None) -> PromptTemplate | None:
        versions = self._templates.get(name)
        if not versions:
            return None
        if version is None:
            return versions[max(versions.keys())]
        return versions.get(version)

    async def list_versions(self, name: str) -> list[int]:
        return sorted(self._templates.get(name, {}).keys())

    async def list_names(self) -> list[str]:
        return list(self._templates.keys())

    async def delete(self, name: str, version: int | None = None) -> None:
        if version is None:
            self._templates.pop(name, None)
        else:
            self._templates.get(name, {}).pop(version, None)


@runtime_checkable
class BlobStore(Protocol):
    """The minimal shape PersistedPromptStore needs from a backing store --
    a single whole-dict snapshot, no partial updates. Deliberately NOT an
    import of fabrica.civitas_bridge.state.ComponentStateHandle -- same
    "depend on a shape, not a package" reasoning as fabrica.memory.store's
    identical BlobStore Protocol (see that module for the full note,
    including the real circular-import reason this can't be an import).
    """

    async def get(self) -> dict[str, Any] | None: ...
    async def set(self, state: dict[str, Any]) -> None: ...


class PersistedPromptStore:
    """PromptStore backed by a BlobStore (in practice, a Civitas
    ComponentStateHandle) -- write-through: every put()/delete() persists
    the entire current snapshot immediately. Reads never touch the
    BlobStore -- state is loaded ONCE at construction (via create()) and
    kept in memory, same trade as PersistedMemoryStore for the same reason
    (one owning writer per component_name, no fan-out readers to worry
    about missing an update).

    Delegates to an InMemoryPromptStore internally (composition, not
    inheritance) so put()'s atomic-version-assignment guarantee (open item
    2) is never duplicated or allowed to drift between the two
    implementations.
    """

    def __init__(self, blob_store: BlobStore, delegate: InMemoryPromptStore) -> None:
        """Not the public construction path -- use create()."""
        self._blob_store = blob_store
        self._delegate = delegate

    @classmethod
    async def create(cls, blob_store: BlobStore) -> PersistedPromptStore:
        delegate = InMemoryPromptStore()
        blob = await blob_store.get()
        if blob is not None:
            _restore_prompt_snapshot(delegate, blob)
        return cls(blob_store, delegate)

    async def _persist(self) -> None:
        await self._blob_store.set(_prompt_snapshot(self._delegate))

    async def put(
        self,
        name: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        cacheable: bool = False,
        cache_boundary: int | None = None,
    ) -> PromptTemplate:
        template = await self._delegate.put(
            name, content, metadata=metadata, cacheable=cacheable, cache_boundary=cache_boundary
        )
        await self._persist()
        return template

    async def get(self, name: str, version: int | None = None) -> PromptTemplate | None:
        return await self._delegate.get(name, version)

    async def list_versions(self, name: str) -> list[int]:
        return await self._delegate.list_versions(name)

    async def list_names(self) -> list[str]:
        return await self._delegate.list_names()

    async def delete(self, name: str, version: int | None = None) -> None:
        await self._delegate.delete(name, version)
        await self._persist()


def _prompt_snapshot(store: InMemoryPromptStore) -> dict[str, Any]:
    """JSON-serializable snapshot of an InMemoryPromptStore's full state.
    Version numbers become string keys (JSON object keys are always
    strings) -- converted back to int on restore, never left as strings
    for callers to trip over.
    """
    templates: dict[str, dict[str, Any]] = {}
    for name, versions in store._templates.items():
        templates[name] = {
            str(version): {
                "content": template.content,
                "metadata": template.metadata,
                "created_at": template.created_at.isoformat() if template.created_at else None,
                "cacheable": template.cacheable,
                "cache_boundary": template.cache_boundary,
            }
            for version, template in versions.items()
        }
    return {"templates": templates}


def _restore_prompt_snapshot(store: InMemoryPromptStore, blob: dict[str, Any]) -> None:
    for name, versions in blob.get("templates", {}).items():
        for version_str, fields in versions.items():
            raw_created_at = fields["created_at"]
            created_at = datetime.fromisoformat(raw_created_at) if raw_created_at else None
            template = PromptTemplate(
                name=name,
                version=int(version_str),
                content=fields["content"],
                metadata=fields["metadata"],
                created_at=created_at,
                cacheable=fields["cacheable"],
                cache_boundary=fields["cache_boundary"],
            )
            store._templates.setdefault(name, {})[template.version] = template
