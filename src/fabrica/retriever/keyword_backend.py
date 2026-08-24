"""KeywordBackend -- the default RetrieverBackend.

Pure Python BM25 (via rank-bm25), not Rust+PyO3, for now. retrieval.md's
Rust-for-compute principle is real, but committing to maturin/PyO3 tooling
before there's an actual performance number showing pure Python is too slow
at real scale would be premature optimization of the build system, not just
the code -- same "ship the default, revisit if forced" logic already applied
to Windows support, macOS Tier 2, and Compactor's swappability throughout
this project's design phase. This resolves retrieval.md's open item 1
("exact Rust/PyO3 packaging shape... implementation-phase decision") in
favor of not deciding it yet, deliberately, not by oversight.
"""

from __future__ import annotations

import re
from typing import Literal

from rank_bm25 import BM25Okapi

from fabrica.retriever.types import Indexable, RankedMatch

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class KeywordBackend:
    """Implements RetrieverBackend. In-memory, zero external infrastructure --
    the always-present fallback every Retriever has, per contracts/retriever.md.
    """

    def __init__(self) -> None:
        self._items: dict[str, Indexable] = {}

    async def add(self, items: list[Indexable]) -> None:
        for item in items:
            self._items[item.id] = item

    async def remove(self, ids: list[str]) -> None:
        for id_ in ids:
            self._items.pop(id_, None)  # no-op if not present

    async def query(
        self, query: str, kind: Literal["tool", "skill"] | None, limit: int
    ) -> list[RankedMatch]:
        candidates = [item for item in self._items.values() if kind is None or item.kind == kind]
        if not candidates:
            return []

        corpus = [_tokenize(item.description) for item in candidates]
        # rank-bm25 rebuilds its index on every construction -- there is no
        # incremental-update API. Acceptable for the in-memory default at
        # this scale; revisit if a real deployment's candidate set is large
        # enough for this rebuild-per-query cost to matter.
        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(_tokenize(query))

        ranked = sorted(
            zip(candidates, scores, strict=True), key=lambda pair: pair[1], reverse=True
        )
        return [
            RankedMatch(item=item, rank=rank) for rank, (item, _score) in enumerate(ranked[:limit])
        ]

    async def health_check(self) -> bool:
        return True  # pure in-memory backend, nothing external to fail
