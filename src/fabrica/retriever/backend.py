"""The RetrieverBackend protocol -- see docs/contracts/retriever.md.

A single search strategy. Implementations: KeywordBackend (default, pure
Python BM25 for now -- see keyword_backend.py's own docstring for why this
isn't Rust+PyO3 yet), PrxBackend, LlamaIndexBackend, LangChainBackend (none
of the latter three implemented here). Never used directly outside Fabrica --
always wrapped by Retriever.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from fabrica.retriever.types import Indexable, RankedMatch


@runtime_checkable
class RetrieverBackend(Protocol):
    async def add(self, items: list[Indexable]) -> None: ...

    async def remove(self, ids: list[str]) -> None: ...

    async def query(
        self, query: str, kind: Literal["tool", "skill"] | None, limit: int
    ) -> list[RankedMatch]: ...

    async def health_check(self) -> bool: ...
