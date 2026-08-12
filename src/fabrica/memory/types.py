"""Types for the memory facets -- see docs/contracts/memory.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True)
class MemoryItem:
    id: str | None
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    score: float | None = None
    """Kept, deliberately unlike RankedMatch (fabrica.retriever.types) --
    no equivalent spike evidence exists yet showing memory-recall scores
    are as misleading as tool-retrieval scores were. See
    contracts/memory.md for the full reasoning.
    """


@dataclass(frozen=True)
class Message:
    """Provisional -- not yet reconciled with Civitas's own runtime-loop
    representation for conversation history. See contracts/memory.md.
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tokens: int
    """Required, not optional -- avoids Fabrica needing to bundle or guess
    at a model-specific tokenizer. The harness already has this from the
    model provider's own usage reporting.
    """


@dataclass(frozen=True)
class CompactionResult:
    summary: str
    preserved: list[Message]
    tokens_before: int
    tokens_after: int
