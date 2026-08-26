"""Summarizer, Compactor, RecencyCompactor, NullCompactor -- see
docs/contracts/memory.md.

RecencyCompactor gained a real validation gate 2026-08-26 -- closes a real,
previously-shipped gap: it used to call the injected Summarizer once and
trust the result unconditionally, with no information-loss check. Found via
a 5-advisor LLM council (peer-reviewed, 5/5 independent convergence) run
against a cited SOTA survey's own sharpest claim (arXiv:2607.21503):
unvalidated compaction can drop task accuracy BELOW the no-context baseline.
Validated empirically, not assumed: specs/archive/spikes/
SPIKE-recency-compactor-validation-gate.md found a real, generic (non-fact-
aware) summarizer prompt under a tight budget preserved both of two
competing critical facts in only 3/6 real runs; the same scenario with this
gate hit 6/6, with the exact missing fact named by the validation detail
every time.
"""

from __future__ import annotations

import re
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


_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "to",
    "of",
    "in",
    "on",
    "at",
    "for",
    "with",
    "as",
    "by",
    "that",
    "this",
    "it",
    "its",
    "we",
    "our",
    "you",
    "your",
    "they",
    "their",
    "i",
    "my",
    "me",
    "us",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "not",
    "no",
    "any",
    "all",
    "one",
    "will",
    "would",
    "should",
    "can",
    "could",
    "just",
    "so",
    "if",
    "when",
    "what",
    "about",
    "want",
    "like",
    "get",
    "got",
    "good",
    "great",
    "some",
    "very",
    "also",
    "too",
    "than",
    "then",
    "there",
    "here",
    "from",
    "keep",
    "hard",
    "off",
}
_WORD_RE = re.compile(r"[a-zA-Z']+")
_NUMBER_RE = re.compile(r"\d+")


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _numbers(text: str) -> set[str]:
    return set(_NUMBER_RE.findall(text))


def _message_text(messages: list[Message]) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


def score_compaction(source_messages: list[Message], summary: str) -> float:
    """Cheap, non-LLM validation score in [0, 1] for how well `summary`
    preserves `source_messages`. Deliberately NOT another LLM call --
    validating a summary with a second summarization-style judgment would
    reintroduce the same untrusted-inference risk one level up (a real
    concern raised independently by 2 of 5 peer reviewers in the council
    session that motivated this). Numeric overlap weighted higher than
    content-word overlap: hard constraints (budgets, dates, counts) are
    disproportionately numeric, and a dropped number is categorically
    worse than a dropped adjective -- confirmed directly in
    SPIKE-recency-compactor-validation-gate.md, where every real failure
    showed up as a missing number, never as missing prose.

    See that spike for the empirical basis of the 0.6/0.4 weighting and
    the 0.55 default threshold this feeds into (RecencyCompactor's own
    `validation_threshold`) -- named there as a starting point, not
    independently tuned against a wider dataset.
    """
    source_text = _message_text(source_messages)
    src_nums, sum_nums = _numbers(source_text), _numbers(summary)
    src_words, sum_words = _content_words(source_text), _content_words(summary)

    num_retention = (len(src_nums & sum_nums) / len(src_nums)) if src_nums else 1.0
    word_retention = (len(src_words & sum_words) / len(src_words)) if src_words else 1.0
    return 0.6 * num_retention + 0.4 * word_retention


DEFAULT_VALIDATION_THRESHOLD = 0.55


class RecencyCompactor:
    """Default Compactor. Preserves the most recent messages verbatim,
    folds everything older into one summary via the injected Summarizer,
    then validates that summary and optionally retries.

    Validation is on by default (`validate=True`) -- it's cheap (no LLM
    call, just regex) and gives a caller real visibility
    (`CompactionResult.validation_score`/`degraded`) it never had before,
    even with no retry configured. Retry is OFF by default
    (`retry_summarizer=None`): SPIKE-recency-compactor-validation-gate.md
    only validated retrying with a DIFFERENT, more fact-explicit prompt --
    retrying the identical Summarizer with the identical prompt was never
    tested and has no principled reason to help, so this class does not
    invent that behavior. A caller who wants the validated retry path
    supplies a second Summarizer implementation (typically the same
    model, a stricter prompt) as `retry_summarizer`.
    """

    def __init__(
        self,
        summarizer: Summarizer,
        *,
        preserve_last_n: int = 6,
        validate: bool = True,
        validation_threshold: float = DEFAULT_VALIDATION_THRESHOLD,
        retry_summarizer: Summarizer | None = None,
    ) -> None:
        self._summarizer = summarizer
        self._preserve_last_n = preserve_last_n
        self._validate = validate
        self._validation_threshold = validation_threshold
        self._retry_summarizer = retry_summarizer

    async def compact(self, messages: list[Message], *, budget_tokens: int) -> CompactionResult:
        tokens_before = sum(m.tokens for m in messages)
        preserved, preserved_tokens = _select_preserved(
            messages, self._preserve_last_n, budget_tokens
        )
        older = messages[: len(messages) - len(preserved)]

        if not older:
            # Nothing to summarize -- every message that needed keeping fit
            # within the window already. No information-loss risk to score.
            return CompactionResult(
                summary="",
                preserved=preserved,
                tokens_before=tokens_before,
                tokens_after=preserved_tokens,
                validation_score=None,
                degraded=False,
            )

        target_tokens = max(0, budget_tokens - preserved_tokens)
        try:
            summary = await self._summarizer.summarize(older, target_tokens=target_tokens)
        except Exception as exc:
            raise CompactionError(exc) from exc

        tokens_after = preserved_tokens + target_tokens

        if not self._validate:
            return CompactionResult(
                summary=summary,
                preserved=preserved,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                validation_score=None,
                degraded=False,
            )

        best_summary, best_score = summary, score_compaction(older, summary)

        if best_score < self._validation_threshold and self._retry_summarizer is not None:
            # Retry keeps the SAME target_tokens, never a larger one -- a
            # caller's budget_tokens is a real ceiling (e.g. fitting a
            # model's context window), not a suggestion. Only the prompt
            # differs, via the caller-supplied retry_summarizer. Confirmed
            # this alone (no budget increase) still recovers correctness
            # in the spike's own confirmatory run.
            try:
                retry_summary = await self._retry_summarizer.summarize(
                    older, target_tokens=target_tokens
                )
            except Exception:
                # The first summarizer already succeeded -- a failing
                # retry degrades the result, it does not abort a
                # compaction that already has a usable (if imperfect)
                # summary. Only the FIRST summarizer's own failure raises.
                retry_summary = None

            if retry_summary is not None:
                retry_score = score_compaction(older, retry_summary)
                if retry_score >= best_score:
                    best_summary, best_score = retry_summary, retry_score

        return CompactionResult(
            summary=best_summary,
            preserved=preserved,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            validation_score=best_score,
            degraded=best_score < self._validation_threshold,
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
