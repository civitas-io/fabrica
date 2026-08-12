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
