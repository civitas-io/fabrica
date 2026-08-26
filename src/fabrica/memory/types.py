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
    validation_score: float | None = None
    """None when nothing was summarized (`older` was empty -- no
    information-loss risk to score) or validation was disabled. Otherwise
    the best score achieved across the loss-check + retry loop -- see
    RecencyCompactor and specs/archive/spikes/
    SPIKE-recency-compactor-validation-gate.md, which found this
    catches real, previously-silent compaction failures (a generic
    summarizer prompt under a tight budget passed_facts-correct in only
    3/6 real runs; the same scenario with this gate hit 6/6)."""
    degraded: bool = False
    """True when validation_score fell below the configured threshold even
    after the retry -- an honest signal, not a silent best-effort. A
    caller that cares can check this and, e.g., fall back to a larger
    budget_tokens or surface a warning; RecencyCompactor itself does not
    invent a third attempt or degrade the result further."""
