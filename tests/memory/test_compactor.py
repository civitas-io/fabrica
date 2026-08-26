"""Tests for RecencyCompactor -- proving the specific stated algorithm,
including the ceiling-not-guarantee behavior and the flagged pathological
edge case, not just "it returns something".
"""

from __future__ import annotations

from typing import Literal

import pytest

from fabrica.memory import (
    DEFAULT_VALIDATION_THRESHOLD,
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


# ---------------------------------------------------------------------------
# Validation gate -- see specs/archive/spikes/
# SPIKE-recency-compactor-validation-gate.md for the empirical basis.
# ---------------------------------------------------------------------------


async def test_no_summarization_needed_has_no_validation_score() -> None:
    """validation_score is None, not 1.0 or 0.0 -- there was no
    information-loss risk to score at all, a different thing from "scored
    perfectly"."""
    summarizer = _FakeSummarizer()
    compactor = RecencyCompactor(summarizer, preserve_last_n=6)
    messages = [msg("user", "short", 5), msg("assistant", "short reply", 5)]

    result = await compactor.compact(messages, budget_tokens=1000)

    assert result.validation_score is None
    assert result.degraded is False


async def test_good_summary_scores_high_and_is_not_degraded() -> None:
    summarizer = _FakeSummarizer(response="Budget is $2400 total, four people, Lisbon trip")
    compactor = RecencyCompactor(summarizer, preserve_last_n=1)
    messages = [
        msg("user", "Our hard budget ceiling is $2400 total for four people going to Lisbon", 20),
        msg("assistant", "Got it", 5),
        msg("user", "recent", 5),
    ]

    result = await compactor.compact(messages, budget_tokens=100)

    assert result.validation_score is not None
    assert result.validation_score > DEFAULT_VALIDATION_THRESHOLD
    assert result.degraded is False


async def test_summary_dropping_a_number_scores_low_and_is_flagged_degraded() -> None:
    """The exact failure mode SPIKE-recency-compactor-validation-gate.md
    found real, generic summarizer prompts actually produce under a tight
    budget: the number is gone, the prose reads fine."""
    summarizer = _FakeSummarizer(response="A family trip to Lisbon with a budget")
    compactor = RecencyCompactor(summarizer, preserve_last_n=1)
    messages = [
        msg("user", "Our hard budget ceiling is $2400 total for four people going to Lisbon", 20),
        msg("assistant", "Got it", 5),
        msg("user", "recent", 5),
    ]

    result = await compactor.compact(messages, budget_tokens=100)

    assert result.validation_score is not None
    assert result.validation_score < DEFAULT_VALIDATION_THRESHOLD
    assert result.degraded is True


async def test_retry_summarizer_not_called_when_not_configured() -> None:
    """Default behavior: validate, but don't invent an unvalidated retry.
    Retrying the SAME summarizer/prompt was never tested by the spike and
    has no principled reason to help -- only a caller-supplied
    retry_summarizer triggers a second attempt."""
    summarizer = _FakeSummarizer(response="no numbers here at all")
    compactor = RecencyCompactor(summarizer, preserve_last_n=1)
    messages = [
        msg("user", "The ceiling is $2400", 10),
        msg("user", "recent", 5),
    ]

    result = await compactor.compact(messages, budget_tokens=100)

    assert len(summarizer.calls) == 1  # no retry attempted
    assert result.degraded is True


async def test_retry_summarizer_used_and_recovers_a_degraded_compaction() -> None:
    naive = _FakeSummarizer(response="a trip with a budget")  # drops the number
    stricter = _FakeSummarizer(response="Budget ceiling is $2400, trip to Lisbon")
    compactor = RecencyCompactor(naive, preserve_last_n=1, retry_summarizer=stricter)
    messages = [
        msg("user", "Our hard budget ceiling is $2400 total for the Lisbon trip", 15),
        msg("user", "recent", 5),
    ]

    result = await compactor.compact(messages, budget_tokens=100)

    assert len(naive.calls) == 1
    assert len(stricter.calls) == 1
    assert result.summary == stricter.response
    assert result.degraded is False


async def test_retry_keeps_the_same_target_tokens_never_a_larger_one() -> None:
    """A caller's budget_tokens is a real ceiling (e.g. a model's context
    window), not a suggestion -- confirmed as the production-correct
    choice in the spike's own confirmatory run (same budget on retry
    still recovered 6/6 correctness)."""
    naive = _FakeSummarizer(response="no numbers")
    stricter = _FakeSummarizer(response="still no numbers but more words than before")
    compactor = RecencyCompactor(naive, preserve_last_n=1, retry_summarizer=stricter)
    messages = [msg("user", "The ceiling is $2400", 10), msg("user", "recent", 5)]

    await compactor.compact(messages, budget_tokens=50)

    naive_target = naive.calls[0][1]
    stricter_target = stricter.calls[0][1]
    assert stricter_target == naive_target


async def test_retry_summarizer_failure_falls_back_to_first_result_not_an_error() -> None:
    """The first summarizer already succeeded -- a failing retry degrades
    the result, it does not abort a compaction that already has a usable
    (if imperfect) summary."""

    class _FailingRetry:
        async def summarize(self, messages: list[Message], *, target_tokens: int) -> str:
            raise RuntimeError("retry model call failed")

    naive = _FakeSummarizer(response="no numbers here")
    compactor = RecencyCompactor(naive, preserve_last_n=1, retry_summarizer=_FailingRetry())
    messages = [msg("user", "The ceiling is $2400", 10), msg("user", "recent", 5)]

    result = await compactor.compact(messages, budget_tokens=100)

    assert result.summary == "no numbers here"
    assert result.degraded is True


async def test_validate_false_disables_scoring_entirely() -> None:
    summarizer = _FakeSummarizer(response="no numbers here")
    compactor = RecencyCompactor(summarizer, preserve_last_n=1, validate=False)
    messages = [msg("user", "The ceiling is $2400", 10), msg("user", "recent", 5)]

    result = await compactor.compact(messages, budget_tokens=100)

    assert result.validation_score is None
    assert result.degraded is False
