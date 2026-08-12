"""Tests for RecencyCompactor -- proving the specific stated algorithm,
including the ceiling-not-guarantee behavior and the flagged pathological
edge case, not just "it returns something".
"""

from __future__ import annotations

from typing import Literal

import pytest

from fabrica.memory import (
    CompactionError,
    CompactionUnavailableError,
    Message,
    NullCompactor,
    RecencyCompactor,
)


class _FakeSummarizer:
    def __init__(self, response: str = "summary of older messages") -> None:
        self.response = response
        self.calls: list[tuple[list[Message], int]] = []

    async def summarize(self, messages: list[Message], *, target_tokens: int) -> str:
        self.calls.append((messages, target_tokens))
        return self.response


def msg(role: Literal["system", "user", "assistant", "tool"], content: str, tokens: int) -> Message:
    return Message(role=role, content=content, tokens=tokens)


async def test_preserves_last_n_verbatim_when_within_budget() -> None:
    summarizer = _FakeSummarizer()
    compactor = RecencyCompactor(summarizer, preserve_last_n=2)
    messages = [
        msg("user", "old message 1", 10),
        msg("assistant", "old message 2", 10),
        msg("user", "recent message 1", 10),
        msg("assistant", "recent message 2", 10),
    ]

    result = await compactor.compact(messages, budget_tokens=100)

    assert result.preserved == messages[-2:]
    assert result.summary == "summary of older messages"
    # Summarizer was called with exactly the OLDER messages, not all of them.
    assert summarizer.calls[0][0] == messages[:-2]


async def test_preserve_last_n_is_a_ceiling_not_a_guarantee() -> None:
    """If the last preserve_last_n messages alone already exceed
    budget_tokens, FEWER are preserved -- not more dropped from the
    summary side, per the contract's explicit statement.
    """
    summarizer = _FakeSummarizer()
    compactor = RecencyCompactor(summarizer, preserve_last_n=3)
    messages = [
        msg("user", "old", 5),
        msg("user", "a", 40),
        msg("user", "b", 40),
        msg("user", "c", 40),  # last 3 alone = 120 tokens
    ]

    result = await compactor.compact(messages, budget_tokens=100)

    # Only the last 2 fit within 100 tokens cumulatively (40+40=80, +40=120 too much)
    assert len(result.preserved) == 2
    assert result.preserved == messages[-2:]


async def test_no_summarization_needed_when_everything_fits() -> None:
    summarizer = _FakeSummarizer()
    compactor = RecencyCompactor(summarizer, preserve_last_n=6)
    messages = [msg("user", "short", 5), msg("assistant", "short reply", 5)]

    result = await compactor.compact(messages, budget_tokens=1000)

    assert result.preserved == messages
    assert result.summary == ""
    assert len(summarizer.calls) == 0  # summarizer never invoked -- nothing to fold


async def test_single_message_exceeding_budget_preserves_nothing_verbatim() -> None:
    """The flagged pathological edge case, resolved mechanically per the
    stated algorithm (not a special-cased default): if even the single
    most recent message alone exceeds budget_tokens, zero messages are
    preserved, and everything (including that oversized message) goes to
    the summarizer.
    """
    summarizer = _FakeSummarizer()
    compactor = RecencyCompactor(summarizer, preserve_last_n=6)
    messages = [msg("user", "small", 5), msg("user", "huge", 500)]

    result = await compactor.compact(messages, budget_tokens=100)

    assert result.preserved == []
    assert summarizer.calls[0][0] == messages  # everything went to the summarizer


async def test_compaction_error_wraps_summarizer_failure() -> None:
    class _FailingSummarizer:
        async def summarize(self, messages: list[Message], *, target_tokens: int) -> str:
            raise RuntimeError("model call failed")

    compactor = RecencyCompactor(_FailingSummarizer(), preserve_last_n=1)
    messages = [msg("user", "a", 10), msg("user", "b", 10)]

    with pytest.raises(CompactionError):
        await compactor.compact(messages, budget_tokens=15)


async def test_null_compactor_raises_unavailable() -> None:
    compactor = NullCompactor()

    with pytest.raises(CompactionUnavailableError):
        await compactor.compact([msg("user", "x", 5)], budget_tokens=100)
