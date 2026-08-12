"""PromptManager -- see docs/contracts/prompts.md."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from fabrica.prompts.errors import PromptParseError
from fabrica.prompts.store import PromptStore
from fabrica.prompts.types import PromptTemplate

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class PromptManager:
    """Adds exactly one thing over the raw backend: a read cache keyed by
    (name, version).

    Decision for open item 1 (no eviction policy specified in the
    contract): unbounded in-process cache. Prompt catalogs are expected to
    be small and curated (unlike tool/skill catalogs or memory), so
    unbounded caching of (name, version) entries is a reasonable default
    for now -- revisit with an LRU/TTL bound only if a real deployment's
    catalog turns out large enough for this to matter. A real decision,
    not an oversight.
    """

    def __init__(self, store: PromptStore) -> None:
        self._store = store
        self._cache: dict[tuple[str, int | None], PromptTemplate] = {}

    async def get(self, name: str, version: int | None = None) -> PromptTemplate | None:
        key = (name, version)
        if key in self._cache:
            return self._cache[key]
        template = await self._store.get(name, version)
        if template is not None:
            self._cache[key] = template
        return template

    async def put(
        self,
        name: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        cacheable: bool = False,
        cache_boundary: int | None = None,
    ) -> PromptTemplate:
        template = await self._store.put(
            name,
            content,
            metadata=metadata,
            cacheable=cacheable,
            cache_boundary=cache_boundary,
        )
        self._cache.pop((name, None), None)  # "latest" just changed
        self._cache[(name, template.version)] = template
        return template

    async def list_versions(self, name: str) -> list[int]:
        return await self._store.list_versions(name)

    async def list_names(self) -> list[str]:
        """Not cached -- enumerating all names isn't assumed to be a hot
        path, unlike get().
        """
        return await self._store.list_names()

    async def delete(self, name: str, version: int | None = None) -> None:
        await self._store.delete(name, version)
        # A deleted specific version can change what version=None now
        # resolves to -- invalidate every cached entry for this name, not
        # just the deleted version.
        for key in [k for k in self._cache if k[0] == name]:
            del self._cache[key]

    async def load(self, path: Path) -> PromptTemplate:
        """Reads a PROMPT.md-shaped file and calls put() with the result.

        Raises:
            PromptParseError: malformed frontmatter, or a missing `name`.
        """
        try:
            raw = path.read_text()
        except OSError as exc:
            raise PromptParseError(f"cannot read {path}: {exc}") from exc

        match = _FRONTMATTER_RE.match(raw)
        if match is None:
            raise PromptParseError(f"{path}: no YAML frontmatter block found")

        try:
            frontmatter = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise PromptParseError(f"{path}: malformed YAML frontmatter: {exc}") from exc

        if not isinstance(frontmatter, dict):
            raise PromptParseError(f"{path}: frontmatter must be a mapping")

        name = frontmatter.get("name")
        if not name or not isinstance(name, str):
            raise PromptParseError(f"{path}: missing required field 'name'")

        cacheable = bool(frontmatter.get("cacheable", False))
        cache_boundary = frontmatter.get("cache_boundary")
        content = match.group(2)
        metadata = {
            k: v for k, v in frontmatter.items() if k not in {"name", "cacheable", "cache_boundary"}
        }

        # Idempotent for unchanged content: avoid version-number churn from
        # repeated CI loads of an unchanged file, mirroring
        # ToolManager.register()'s idempotency.
        existing = await self.get(name)
        if (
            existing is not None
            and existing.content == content
            and existing.cacheable == cacheable
            and existing.cache_boundary == cache_boundary
            and existing.metadata == metadata
        ):
            return existing

        return await self.put(
            name, content, metadata=metadata, cacheable=cacheable, cache_boundary=cache_boundary
        )
