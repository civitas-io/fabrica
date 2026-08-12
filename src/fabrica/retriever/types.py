"""Types shared by RetrieverBackend and Retriever -- see docs/contracts/retriever.md."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Indexable:
    """One thing the Retriever can discover: a tool or a skill.

    Never memory -- memory has its own MemoryStore.search() (see
    docs/memory.md); forcing it through this type would paper over a real
    semantic difference (memory is scoped, this is a shared registry).
    """

    id: str
    kind: Literal["tool", "skill"]
    name: str
    description: str  # the only field actually embedded/matched
    eager: bool = False  # inverted `defer_loading` -- see Retriever.list_eager()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Indexable.id must be non-empty")


@dataclass(frozen=True)
class RankedMatch:
    """A single search result.

    Deliberately has NO score field. This isn't an omission -- rank-not-
    threshold (system-design.md §6, from SPIKE-tool-retrieval-token-overhead.md
    and SPIKE-code-mode-execution.md) is enforced by the type itself, not
    just a docstring warning. Correct hits and near-misses were observed
    landing in the same 0.01-0.04 score band on real data; exposing a score
    field here would invite exactly the misuse those spikes warned against.
    """

    item: Indexable
    rank: int  # 0 = best match. Lists returned by search() are always
    # pre-sorted ascending by rank -- callers must not re-sort.
