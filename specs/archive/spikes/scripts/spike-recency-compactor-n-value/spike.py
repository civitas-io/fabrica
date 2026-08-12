#!/usr/bin/env python3
"""
SPIKE: Does preserve_last_n's specific value matter, and what happens when
it actually competes with a tight budget_tokens -- the two gaps
SPIKE-recency-compactor-validation.md explicitly named as NOT tested
("preserve_last_n=6 itself isn't validated"; "behavior right at a hard
budget_tokens boundary" -- that spike's scenario "had plenty of room").

Locked question, timeboxed, throwaway script. Unlike the first spike, this
one calls the REAL fabrica.memory.compactor.RecencyCompactor/_select_preserved
code directly (import from the actual package), not a hand-rolled
reimplementation of the slicing logic -- the whole point this time is
whether the REAL preserve_last_n-vs-budget_tokens interaction holds up,
so re-implementing it by hand risks testing something subtly different.

Same 13-turn conversation as the first spike (already validated, has a
known-correct answer gated on a buried $2400 constraint). This time:
  - budget_tokens is deliberately TIGHT (not "plenty of room") -- tight
    enough that preserve_last_n=10's worth of recent messages actually
    gets clipped by the budget, not just by N itself.
  - preserve_last_n varies: 2, 6 (current default), 10 -- against the
    SAME tight budget, to see whether the specific value changes the
    outcome once the two constraints actually compete.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "src"))

from fabrica.memory.compactor import RecencyCompactor
from fabrica.memory.types import Message

PROJECT = "fdl-c-gemini-apis"
MODEL = "gemini-2.5-flash"
URL = f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/global/publishers/google/models/{MODEL}:generateContent"

CONVERSATION = [
    ("user", "Hey, I'm planning a family trip to Lisbon for next spring. Can you help me put together an itinerary?"),
    ("assistant", "Happy to help. Before I suggest anything -- what's your total budget for the trip, and how many travelers?"),
    ("user", "Four of us. Our hard budget ceiling is $2400 total for everything -- flights, hotel, activities. That's non-negotiable, my in-laws are paying half and they were very clear about it."),
    ("assistant", "Got it, $2400 total for 4 people, all-inclusive. Let's start with flights -- do you have preferred dates?"),
    ("user", "First week of May, roughly the 3rd through the 10th."),
    ("assistant", "That's a good time to visit -- shoulder season, fewer crowds than summer. For flights from the US, expect somewhere in the $600-900 range round trip for the group if you book now, depending on origin city."),
    ("user", "We're flying out of Newark. What neighborhoods would you recommend for the hotel?"),
    ("assistant", "Alfama is charming and historic but hilly -- can be tough with kids. Baixa is central and flat, walkable to most attractions. Belem is quieter, more residential, good if you want a slower pace."),
    ("user", "Baixa sounds good, we want to be able to walk everywhere. What are the must-see attractions?"),
    ("assistant", "Belem Tower and the Jeronimos Monastery are the big ones -- both UNESCO sites. The Alfama district itself is worth wandering for the views and fado music. Sintra is a popular day trip too, about 40 minutes by train, with the Pena Palace."),
    ("user", "Sintra sounds amazing, let's definitely add that. What about food -- any recommendations for family-friendly places?"),
    ("assistant", "Time Out Market is a great option for families -- it's a big food hall with lots of variety, so everyone can pick what they want. For a sit-down meal, most tascas (casual local restaurants) are very kid-friendly and affordable."),
    ("user", "Perfect. Okay, I found two hotel options in Baixa. Option A is a 4-star boutique hotel, $1400 for the week for two rooms. Option B is a well-reviewed 3-star, $850 for the week for two rooms. Which one should we book, given everything we've talked about?"),
]

DECISION_SYSTEM = "You are a helpful travel planning assistant continuing an ongoing conversation with a family."


def _estimate_tokens(text: str) -> int:
    """~4 chars/token -- a real, if approximate, stand-in for the model
    provider's own usage reporting Message.tokens is designed to carry
    (contracts/memory.md: 'required, not optional -- avoids Fabrica
    needing to bundle or guess at a model-specific tokenizer'). Good
    enough to construct a REALISTIC tight-budget scenario; the spike does
    not depend on exact tokenizer fidelity, only on the relative sizes
    that make N=10 collide with the budget while N=2 does not.
    """
    return max(1, len(text) // 4)


MESSAGES = [
    Message(role=("user" if role == "user" else "assistant"), content=text, tokens=_estimate_tokens(text))
    for role, text in CONVERSATION
]

# Deliberately TIGHT -- chosen so preserve_last_n=10's worth of recent
# messages exceeds it (forcing _select_preserved's real cutoff to kick in
# before reaching 10), while preserve_last_n=2's worth fits easily. This is
# the exact "hard budget_tokens boundary" the first spike's scenario never
# hit ("this scenario's summary had plenty of room").
TIGHT_BUDGET_TOKENS = sum(m.tokens for m in MESSAGES[-6:])  # ~= what N=6 alone needs


_TOKEN_CACHE: list[str] = []


def _get_token() -> str:
    # Fetched once per script run, not once per API call -- gcloud's own
    # subprocess startup overhead (~1-2s) times 30+ real calls in this
    # spike would otherwise dominate the runtime for no real benefit
    # (the token is valid for the whole run).
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
    """Implements fabrica.memory.compactor.Summarizer for real -- the
    actual injected dependency shape CivitasBridge/RecencyCompactor use,
    not a stand-in with a different interface.
    """

    async def summarize(self, messages: list[Message], *, target_tokens: int) -> str:
        turns = "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)
        prompt = (
            "Summarize the following conversation turns concisely, preserving every "
            "concrete fact, number, and constraint mentioned (budget figures, dates, "
            "names, preferences). Do not add commentary or omit numeric constraints. "
            f"Keep it under {max(30, target_tokens // 2)} words.\n\nConversation:\n{turns}"
        )
        return call_gemini(prompt)


def format_preserved(messages: list[Message]) -> str:
    return "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)


async def run_condition(preserve_last_n: int) -> tuple[str, int, int]:
    """Runs the REAL RecencyCompactor.compact() against the tight budget,
    then asks the real final decision question using its actual output.
    Returns (answer, messages_preserved, tokens_after).
    """
    compactor = RecencyCompactor(RealGeminiSummarizer(), preserve_last_n=preserve_last_n)
    result = await compactor.compact(MESSAGES, budget_tokens=TIGHT_BUDGET_TOKENS)

    prompt = (
        f"{DECISION_SYSTEM}\n\nSummary of earlier conversation:\n{result.summary}\n\n"
        f"Most recent turns (verbatim):\n{format_preserved(result.preserved)}\n\n"
        "Respond as the assistant, answering the last user message."
    )
    answer = call_gemini(prompt)
    return answer, len(result.preserved), result.tokens_after


def check_correct(answer: str) -> str:
    """Same corrected checker as the first spike -- literal figure
    required, generic budget language is not evidence of grounding."""
    recommends_b = "option b" in answer.lower() or "$850" in answer
    used_real_constraint = "2400" in answer
    if recommends_b and used_real_constraint:
        return "CORRECT-GROUNDED"
    if recommends_b and not used_real_constraint:
        return "RIGHT-ANSWER-WRONG-REASON"
    return "WRONG"


async def main() -> None:
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    n_values = [2, 6, 10]

    print(f"TIGHT_BUDGET_TOKENS = {TIGHT_BUDGET_TOKENS}")
    print(f"Per-message token estimates: {[m.tokens for m in MESSAGES]}")
    print(f"Sum of last 10 messages' tokens: {sum(m.tokens for m in MESSAGES[-10:])}")
    print(f"Sum of last 6 messages' tokens: {sum(m.tokens for m in MESSAGES[-6:])}")
    print(f"Sum of last 2 messages' tokens: {sum(m.tokens for m in MESSAGES[-2:])}")

    results: dict[int, list[str]] = {n: [] for n in n_values}
    preserved_counts: dict[int, list[int]] = {n: [] for n in n_values}

    for i in range(n_runs):
        print(f"\n===== RUN {i + 1}/{n_runs} =====", flush=True)
        for n in n_values:
            answer, n_preserved, tokens_after = await run_condition(n)
            verdict = check_correct(answer)
            results[n].append(verdict)
            preserved_counts[n].append(n_preserved)
            print(
                f"\n--- preserve_last_n={n} (actually preserved={n_preserved}, verdict={verdict}) ---",
                flush=True,
            )
            print(answer, flush=True)

    print("\n\n===== SUMMARY =====")
    for n in n_values:
        verdicts = results[n]
        correct = sum(1 for v in verdicts if v == "CORRECT-GROUNDED")
        avg_preserved = sum(preserved_counts[n]) / len(preserved_counts[n])
        print(
            f"preserve_last_n={n}: {correct}/{len(verdicts)} grounded-correct, "
            f"avg actually-preserved={avg_preserved:.1f}/{n} -- {verdicts}"
        )


if __name__ == "__main__":
    import anyio

    anyio.run(main)
