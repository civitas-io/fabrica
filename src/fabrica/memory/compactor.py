"""Summarizer, Compactor, RecencyCompactor, NullCompactor -- see
docs/contracts/memory.md.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fabrica.memory.errors import CompactionError, CompactionUnavailableError
from fabrica.memory.types import CompactionResult, Message


@runtime_checkable
class Summarizer(Protocol):
    """Injected dependency -- Fabrica never constructs one itself, never
    holds model credentials.
    """

    async def summarize(self, messages: list[Message], *, target_tokens: int) -> str: ...


@runtime_checkable
class Compactor(Protocol):
    async def compact(self, messages: list[Message], *, budget_tokens: int) -> CompactionResult: ...


class RecencyCompactor:
    """Default Compactor. Preserves the most recent messages verbatim,
    folds everything older into one summary via the injected Summarizer.
    """

    def __init__(self, summarizer: Summarizer, *, preserve_last_n: int = 6) -> None:
        self._summarizer = summarizer
        self._preserve_last_n = preserve_last_n

    async def compact(self, messages: list[Message], *, budget_tokens: int) -> CompactionResult:
        tokens_before = sum(m.tokens for m in messages)
        preserved, preserved_tokens = _select_preserved(
            messages, self._preserve_last_n, budget_tokens
        )
        older = messages[: len(messages) - len(preserved)]

        if not older:
            # Nothing to summarize -- every message that needed keeping fit
            # within the window already.
            return CompactionResult(
                summary="",
                preserved=preserved,
                tokens_before=tokens_before,
                tokens_after=preserved_tokens,
            )

        target_tokens = max(0, budget_tokens - preserved_tokens)
        try:
            summary = await self._summarizer.summarize(older, target_tokens=target_tokens)
        except Exception as exc:
            raise CompactionError(exc) from exc

        # tokens_after is an estimate -- the summary's own token count
        # isn't measured here (would need the same tokenizer dependency
        # Message.tokens was designed specifically to avoid). Reported as
        # the target the summarizer was asked to hit, not a measurement.
        return CompactionResult(
            summary=summary,
            preserved=preserved,
            tokens_before=tokens_before,
            tokens_after=preserved_tokens + target_tokens,
        )


def _select_preserved(
    messages: list[Message], preserve_last_n: int, budget_tokens: int
) -> tuple[list[Message], int]:
    """Walk backward from the newest message, preserving each one verbatim
    while (a) fewer than preserve_last_n have been kept, and (b) their
    cumulative tokens stay under budget_tokens.

    Unresolved edge case (contracts/memory.md, stated there rather than
    hidden here): if even the single most recent message alone exceeds
    budget_tokens, this preserves zero messages -- a literal, mechanical
    extension of the stated rule, not a special-cased default invented to
    paper over the gap. The caller ends up summarizing everything,
    including that one oversized message.
    """
    preserved: list[Message] = []
    total = 0
    for message in reversed(messages):
        if len(preserved) >= preserve_last_n:
            break
        if total + message.tokens > budget_tokens:
            break
        preserved.append(message)
        total += message.tokens
    preserved.reverse()
    return preserved, total


class NullCompactor:
    """Wired in when summarizer=None at construction. A Null Object, not a
    special case -- MemoryManager always receives A Compactor.
    """

    async def compact(self, messages: list[Message], *, budget_tokens: int) -> CompactionResult:
        raise CompactionUnavailableError(
            "No Summarizer was configured -- construct CivitasBridge with "
            "summarizer=... to enable compaction."
        )
