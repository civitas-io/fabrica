#!/usr/bin/env python3
"""
SPIKE: Does a cheap, non-LLM validation gate on RecencyCompactor's summary
step actually catch and recover from a real compaction failure -- the two
gaps SPIKE-recency-compactor-validation.md and SPIKE-recency-compactor-n-value.md
explicitly left untested: (a) multiple competing facts needing simultaneous
preservation, and (b) a summary itself losing a fact under real token
pressure (the first spike's summarizer had "plenty of room" and never
failed; this one deliberately removes that room so failures can occur).

Real, not simulated: calls the REAL fabrica.memory.compactor.RecencyCompactor
directly (same discipline as SPIKE-recency-compactor-n-value.md), real
Gemini 2.5 Flash calls via Vertex AI (same project/model as both prior
spikes), real downstream grounded-correctness checks. The validation gate
itself is prototyped here, throwaway-script style (spike-prototype
discipline) -- promoted into real fabrica source only if this spike shows
it actually helps.

Three critical, non-repeated facts stated once, early, in DIFFERENT topic
domains (financial, medical/safety, scheduling) so a heuristic keyed to
only one signal type can't trivially pass by accident:
  1. Budget ceiling: "$2400 total, non-negotiable" (numeric)
  2. Severe shellfish allergy, carries an EpiPen (safety-critical; the
     load-bearing word "shellfish" is a plain content word, not a number)
  3. Hard return-by date: May 9th, for a graduation (numeric)

All three sit outside preserve_last_n's verbatim window -- correctness
depends entirely on the summary surviving intact.

Three conditions, budget_tokens deliberately TIGHT (not "plenty of room"):
  (a) FULL      -- entire conversation, no compaction (baseline)
  (b) NAIVE     -- RecencyCompactor's real, current, shipped behavior: one
                   summarization call, no validation
  (c) VALIDATED -- NEW gate: a cheap, non-LLM heuristic (numeric-token
                   overlap + content-word overlap between source turns and
                   summary) scores the naive result; below threshold,
                   retry ONCE via a second real RecencyCompactor.compact()
                   call with a stricter prompt and a larger (but still
                   real) budget; keep whichever attempt scores higher

Downstream question requires BOTH fact 2 and fact 3 to answer correctly.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "src"))

from fabrica.memory.compactor import RecencyCompactor
from fabrica.memory.types import CompactionResult, Message

PROJECT = "fdl-c-gemini-apis"
MODEL = "gemini-2.5-flash"
URL = f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/global/publishers/google/models/{MODEL}:generateContent"

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

CONVERSATION = [
    ("user", "Hi, planning a family trip to Lisbon for next spring, four of us. Can you help?"),
    ("assistant", "Happy to help. What's your total budget, and any dates in mind?"),
    (
        "user",
        "Our hard budget ceiling is $2400 total for everything -- flights, hotel, activities. "
        "That's non-negotiable, my in-laws are paying half and were very clear about it.",
    ),
    (
        "assistant",
        "Understood, $2400 total for 4 people, all-inclusive. Any dietary restrictions or "
        "health considerations I should keep in mind while planning?",
    ),
    (
        "user",
        "Yes, important one: my son has a severe shellfish allergy. He carries an EpiPen at all "
        "times. Any restaurant with shellfish on the menu is off the table entirely, not just "
        "avoiding the dish -- cross-contamination risk is too high for us to take that chance.",
    ),
    (
        "assistant",
        "Noted, thank you for flagging that -- I'll avoid seafood-focused restaurants entirely. "
        "When would you like to travel?",
    ),
    (
        "user",
        "First week of May. But one hard constraint: we absolutely must be back home by May 9th "
        "-- my daughter's high school graduation is that evening and she cannot miss it. "
        "Not flexible at all.",
    ),
    (
        "assistant",
        "Got it, return no later than May 9th. For flights from the US, expect $600-900 round "
        "trip for the group depending on origin city and how far out you book.",
    ),
    ("user", "We're flying out of Newark. What neighborhoods would you recommend for the hotel?"),
    (
        "assistant",
        "Baixa is central and flat, walkable to most attractions -- good for families. Alfama is "
        "charming but hilly. Belem is quieter, more residential.",
    ),
    ("user", "Baixa sounds good. What are the must-see attractions?"),
    (
        "assistant",
        "Belem Tower and Jeronimos Monastery are the big ones, both UNESCO sites. Sintra is a "
        "popular day trip, about 40 minutes by train, with the Pena Palace.",
    ),
    ("user", "Sintra sounds great, let's add that. Any general food recommendations?"),
    (
        "assistant",
        "Time Out Market is good for families -- big food hall, lots of variety so everyone can "
        "pick separately. Most local tascas are casual and kid-friendly too.",
    ),
    (
        "user",
        "Okay, two things to decide now. First: I found a highly-rated restaurant called "
        "'La Mare Azul' that's famous for its seafood tower and shellfish platters -- should we "
        "book a table there for one of the nights? Second: I found a much cheaper return flight, "
        "but it's on May 11th instead -- should I book that one to save money?",
    ),
]

DECISION_QUESTION_TURN = len(CONVERSATION) - 1
PRESERVE_LAST_N = 6
DECISION_SYSTEM = "You are a helpful travel planning assistant continuing an ongoing conversation with a family."


def _estimate_tokens(text: str) -> int:
    """~4 chars/token, same approximation as SPIKE-recency-compactor-n-value.md."""
    return max(1, len(text) // 4)


MESSAGES = [
    Message(role=("user" if role == "user" else "assistant"), content=text, tokens=_estimate_tokens(text))
    for role, text in CONVERSATION[:DECISION_QUESTION_TURN]
]

# Deliberately tight: less than what the older (to-be-summarized) portion's
# raw text would need, forcing real compression pressure, but enough to fit
# preserve_last_n=6's verbatim window plus a real (non-trivial) summary.
OLDER_RAW_TOKENS = sum(m.tokens for m in MESSAGES[: len(MESSAGES) - PRESERVE_LAST_N])
PRESERVED_TOKENS = sum(m.tokens for m in MESSAGES[-PRESERVE_LAST_N:])
TIGHT_BUDGET_TOKENS = PRESERVED_TOKENS + max(18, OLDER_RAW_TOKENS // 12)

_TOKEN_CACHE: list[str] = []


def _get_token() -> str:
    if not _TOKEN_CACHE:
        _TOKEN_CACHE.append(
            subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
        )
    return _TOKEN_CACHE[0]


def call_gemini(prompt: str) -> str:
    token = _get_token()
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", URL,
         "-H", f"Authorization: Bearer {token}",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(body)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        data = json.loads(result.stdout)
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"[ERROR parsing response: {e}] {result.stdout[:500]}"


class RealGeminiSummarizer:
    """Implements fabrica.memory.compactor.Summarizer -- a GENERIC prompt
    with no explicit fact-preservation instruction. RecencyCompactor never
    prescribes what the injected Summarizer's own prompt says -- a real
    caller can inject anything satisfying the Protocol, and a naive/
    careless implementation (the realistic default most callers would
    reach for first) looks exactly like this. The first two spikes' own
    prompt ("preserving every concrete fact...") was already fact-aware,
    which is precisely why neither spike's summarizer ever failed -- this
    version deliberately removes that safety net to test what the
    validation gate is actually for.
    """

    async def summarize(self, messages: list[Message], *, target_tokens: int) -> str:
        turns = "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)
        prompt = (
            f"Summarize the following conversation in under {max(15, target_tokens // 2)} "
            f"words.\n\nConversation:\n{turns}"
        )
        return call_gemini(prompt)


class StricterRetrySummarizer:
    """Implements Summarizer with an explicit hard-requirement prompt --
    the retry path's ONLY difference from RealGeminiSummarizer, used when
    the naive attempt fails validation."""

    async def summarize(self, messages: list[Message], *, target_tokens: int) -> str:
        turns = "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)
        prompt = (
            "Summarize the following conversation turns. You have a hard requirement: "
            "preserve EVERY numeric constraint (budget figures, dates), EVERY safety/"
            "medical constraint, and EVERY named entity, even if it means cutting "
            f"general narrative detail instead. Under {max(25, target_tokens // 2)} words.\n\n"
            f"Conversation:\n{turns}"
        )
        return call_gemini(prompt)


# ---------------------------------------------------------------------------
# The validation gate under test -- cheap, non-LLM, deterministic.
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "at", "for", "with", "as", "by", "that", "this", "it",
    "its", "we", "our", "you", "your", "they", "their", "i", "my", "me", "us",
    "have", "has", "had", "do", "does", "did", "not", "no", "any", "all", "one",
    "will", "would", "should", "can", "could", "just", "so", "if", "when", "what",
    "about", "want", "like", "get", "got", "good", "great", "some", "very", "also",
    "too", "than", "then", "there", "here", "from", "keep", "hard", "off",
}


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z']+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def validate_compaction(source_text: str, summary_text: str) -> tuple[float, dict]:
    """Cheap, non-LLM validation score in [0, 1]. Numeric overlap weighted
    higher -- hard constraints (budgets, dates) are disproportionately
    numeric, and a dropped number is categorically worse than a dropped
    adjective."""
    src_nums, sum_nums = _numbers(source_text), _numbers(summary_text)
    src_words, sum_words = _content_words(source_text), _content_words(summary_text)

    num_retention = (len(src_nums & sum_nums) / len(src_nums)) if src_nums else 1.0
    word_retention = (len(src_words & sum_words) / len(src_words)) if src_words else 1.0

    score = 0.6 * num_retention + 0.4 * word_retention
    return score, {
        "num_retention": round(num_retention, 2),
        "word_retention": round(word_retention, 2),
        "missing_numbers": sorted(src_nums - sum_nums),
    }


VALIDATION_THRESHOLD = 0.55


async def compact_naive() -> tuple[CompactionResult, float, dict]:
    compactor = RecencyCompactor(RealGeminiSummarizer(), preserve_last_n=PRESERVE_LAST_N)
    result = await compactor.compact(MESSAGES, budget_tokens=TIGHT_BUDGET_TOKENS)
    older = MESSAGES[: len(MESSAGES) - len(result.preserved)]
    source_text = "\n".join(f"{m.role.upper()}: {m.content}" for m in older)
    score, detail = validate_compaction(source_text, result.summary)
    return result, score, detail


async def compact_validated() -> tuple[CompactionResult, float, dict, int]:
    """The NEW gate: real RecencyCompactor call, validate, retry ONCE via
    a second real RecencyCompactor.compact() call (stricter Summarizer,
    larger budget) if validation fails."""
    result1, score1, detail1 = await compact_naive()
    if score1 >= VALIDATION_THRESHOLD:
        return result1, score1, detail1, 1

    retry_budget = TIGHT_BUDGET_TOKENS  # SAME budget as the first attempt --
    # production should not silently exceed a caller's real budget_tokens
    # ceiling on retry; confirming the stricter-prompt-alone effect here.
    retry_compactor = RecencyCompactor(StricterRetrySummarizer(), preserve_last_n=PRESERVE_LAST_N)
    result2 = await retry_compactor.compact(MESSAGES, budget_tokens=retry_budget)
    older = MESSAGES[: len(MESSAGES) - len(result2.preserved)]
    source_text = "\n".join(f"{m.role.upper()}: {m.content}" for m in older)
    score2, detail2 = validate_compaction(source_text, result2.summary)

    if score2 >= score1:
        return result2, score2, detail2, 2
    return result1, score1, detail1, 2


def format_preserved(messages: list[Message]) -> str:
    return "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)


def ask_decision(context_prompt: str) -> str:
    question = CONVERSATION[DECISION_QUESTION_TURN][1]
    prompt = f"{context_prompt}\n\nUSER: {question}\n\nASSISTANT:"
    return call_gemini(prompt)


def grounded_correct(answer: str) -> tuple[bool, bool]:
    """(allergy_fact_used, date_fact_used) -- both required for
    grounded-correct. This is the multi-fact test the prior two spikes
    explicitly did not cover."""
    lower = answer.lower()
    negatives = ("no" in lower or "not" in lower or "avoid" in lower
                 or "shouldn't" in lower or "should not" in lower or "against" in lower
                 or "wouldn't recommend" in lower or "skip" in lower or "instead" in lower)
    allergy_used = ("shellfish" in lower or "allerg" in lower or "epipen" in lower) and negatives
    date_used = ("may 9" in lower or "graduation" in lower) and (
        negatives or "miss" in lower
    )
    return allergy_used, date_used


async def run_full() -> tuple[str, bool, bool]:
    context = f"{DECISION_SYSTEM}\n\n" + format_preserved(MESSAGES)
    answer = ask_decision(context)
    allergy_ok, date_ok = grounded_correct(answer)
    return answer, allergy_ok, date_ok


async def run_naive() -> dict:
    result, score, detail = await compact_naive()
    context = (
        f"{DECISION_SYSTEM}\n\nSummary of earlier conversation:\n{result.summary}\n\n"
        f"Most recent turns (verbatim):\n{format_preserved(result.preserved)}"
    )
    answer = ask_decision(context)
    allergy_ok, date_ok = grounded_correct(answer)
    return {
        "summary": result.summary, "score": score, "detail": detail,
        "answer": answer, "allergy_ok": allergy_ok, "date_ok": date_ok,
        "both_ok": allergy_ok and date_ok,
    }


async def run_validated() -> dict:
    result, score, detail, attempts = await compact_validated()
    context = (
        f"{DECISION_SYSTEM}\n\nSummary of earlier conversation:\n{result.summary}\n\n"
        f"Most recent turns (verbatim):\n{format_preserved(result.preserved)}"
    )
    answer = ask_decision(context)
    allergy_ok, date_ok = grounded_correct(answer)
    return {
        "summary": result.summary, "score": score, "detail": detail, "attempts": attempts,
        "answer": answer, "allergy_ok": allergy_ok, "date_ok": date_ok,
        "both_ok": allergy_ok and date_ok,
    }


async def main() -> None:
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    print(f"TIGHT_BUDGET_TOKENS={TIGHT_BUDGET_TOKENS} (preserved needs {PRESERVED_TOKENS}, "
          f"older raw={OLDER_RAW_TOKENS})\n")

    print("=== FULL baseline (3 runs) ===")
    full_results = []
    for i in range(3):
        answer, allergy_ok, date_ok = await run_full()
        full_results.append((allergy_ok, date_ok))
        print(f"run {i}: allergy_ok={allergy_ok} date_ok={date_ok}")
        print(f"  answer: {answer[:220]!r}")

    print(f"\n=== NAIVE compaction ({n_runs} runs) ===")
    naive_results = []
    for i in range(n_runs):
        r = await run_naive()
        naive_results.append(r)
        print(f"run {i}: score={r['score']:.2f} missing_nums={r['detail']['missing_numbers']} "
              f"allergy_ok={r['allergy_ok']} date_ok={r['date_ok']} both_ok={r['both_ok']}")
        print(f"  summary: {r['summary'][:220]!r}")
        print(f"  answer:  {r['answer'][:220]!r}")

    print(f"\n=== VALIDATED compaction ({n_runs} runs) ===")
    validated_results = []
    for i in range(n_runs):
        r = await run_validated()
        validated_results.append(r)
        print(f"run {i}: score={r['score']:.2f} attempts={r['attempts']} "
              f"missing_nums={r['detail']['missing_numbers']} "
              f"allergy_ok={r['allergy_ok']} date_ok={r['date_ok']} both_ok={r['both_ok']}")
        print(f"  summary: {r['summary'][:220]!r}")
        print(f"  answer:  {r['answer'][:220]!r}")

    print("\n=== SUMMARY ===")
    print(f"FULL:      {sum(1 for a, d in full_results if a and d)}/{len(full_results)} both-facts-correct")
    print(f"NAIVE:     {sum(1 for r in naive_results if r['both_ok'])}/{len(naive_results)} both-facts-correct")
    print(f"VALIDATED: {sum(1 for r in validated_results if r['both_ok'])}/{len(validated_results)} both-facts-correct")
    print(f"NAIVE avg validation score:     {sum(r['score'] for r in naive_results) / len(naive_results):.3f}")
    print(f"VALIDATED avg validation score: {sum(r['score'] for r in validated_results) / len(validated_results):.3f}")
    retried = sum(1 for r in validated_results if r["attempts"] == 2)
    print(f"VALIDATED: retried on {retried}/{len(validated_results)} runs")


if __name__ == "__main__":
    asyncio.run(main())
