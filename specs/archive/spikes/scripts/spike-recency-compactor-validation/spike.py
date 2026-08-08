#!/usr/bin/env python3
"""
SPIKE: Does RecencyCompactor's strategy (preserve last N=6 messages verbatim,
LLM-summarize everything older) preserve enough information for a model to
correctly complete a task depending on an early fact?

Locked question, timeboxed, throwaway script (spike-prototype discipline).
Compares three conditions on the SAME multi-turn scenario:
  (a) FULL     -- entire conversation history, no compaction (baseline)
  (b) COMPACT  -- RecencyCompactor's real strategy: last 6 turns verbatim +
                  an LLM-generated summary of everything older
  (c) TRUNCATE -- naive alternative: last 6 turns only, summary DROPPED
                  entirely (tests whether summarizing is worth it vs just
                  cutting)

A hard constraint is stated ONCE, early (turn 2), then buried under 9 turns
of realistic, relevant-but-not-repeating trip-planning discussion. The final
question requires that early constraint to answer correctly. Each condition
run 3x (LLM non-determinism) against real Gemini calls via Vertex AI.
"""
import json
import subprocess
import sys

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

DECISION_QUESTION_TURN = 12  # index of the final user message above

SUMMARIZE_PROMPT = """Summarize the following conversation turns concisely, preserving every concrete fact, number, and constraint mentioned (budget figures, dates, names, preferences). Do not add commentary or omit numeric constraints. Keep it under 120 words.

Conversation:
{turns}
"""

DECISION_SYSTEM = "You are a helpful travel planning assistant continuing an ongoing conversation with a family."


def call_gemini(prompt: str) -> str:
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
    }
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


def format_turns(turns):
    return "\n".join(f"{role.upper()}: {text}" for role, text in turns)


def run_full():
    """Condition (a): entire conversation, no compaction."""
    prompt = f"{DECISION_SYSTEM}\n\nFull conversation so far:\n{format_turns(CONVERSATION)}\n\nRespond as the assistant, answering the last user message."
    return call_gemini(prompt)


def run_compact(preserve_last_n=6):
    """Condition (b): RecencyCompactor's real strategy."""
    older = CONVERSATION[:-preserve_last_n]
    recent = CONVERSATION[-preserve_last_n:]
    summary = call_gemini(SUMMARIZE_PROMPT.format(turns=format_turns(older)))
    prompt = (
        f"{DECISION_SYSTEM}\n\nSummary of earlier conversation:\n{summary}\n\n"
        f"Most recent turns (verbatim):\n{format_turns(recent)}\n\n"
        f"Respond as the assistant, answering the last user message."
    )
    return call_gemini(prompt), summary


def run_truncate(preserve_last_n=6):
    """Condition (c): naive truncation, no summary at all."""
    recent = CONVERSATION[-preserve_last_n:]
    prompt = f"{DECISION_SYSTEM}\n\nRecent conversation (earlier turns not available):\n{format_turns(recent)}\n\nRespond as the assistant, answering the last user message."
    return call_gemini(prompt)


def check_correct(answer: str) -> str:
    """CORRECTED after a false-positive was caught by hand: the first version
    of this checker accepted generic 'budget'/'value for money' language as
    evidence of correct reasoning. That's wrong -- a model with NO access to
    the $2400 constraint can still land on 'Option B, better value' through
    generic reasoning that happens to coincide with the right answer, for
    the wrong reason. The only honest signal that the constraint actually
    survived is the LITERAL figure ($2400) appearing in the answer, since
    that number exists nowhere in the conversation except turn 2."""
    recommends_b = "option b" in answer.lower() or "$850" in answer
    used_real_constraint = "2400" in answer
    if recommends_b and used_real_constraint:
        return "CORRECT-GROUNDED"
    if recommends_b and not used_real_constraint:
        return "RIGHT-ANSWER-WRONG-REASON"
    return "WRONG"


if __name__ == "__main__":
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    results = {"FULL": [], "COMPACT": [], "TRUNCATE": []}

    for i in range(n_runs):
        print(f"\n===== RUN {i+1}/{n_runs} =====")

        ans_full = run_full()
        v_full = check_correct(ans_full)
        results["FULL"].append(v_full)
        print(f"\n--- FULL ({v_full}) ---\n{ans_full}")

        ans_compact, summary = run_compact()
        v_compact = check_correct(ans_compact)
        results["COMPACT"].append(v_compact)
        print(f"\n--- COMPACT summary ---\n{summary}")
        print(f"--- COMPACT answer ({v_compact}) ---\n{ans_compact}")

        ans_trunc = run_truncate()
        v_trunc = check_correct(ans_trunc)
        results["TRUNCATE"].append(v_trunc)
        print(f"\n--- TRUNCATE ({v_trunc}) ---\n{ans_trunc}")

    print("\n\n===== SUMMARY =====")
    for cond, verdicts in results.items():
        correct = sum(1 for v in verdicts if v == "CORRECT")
        print(f"{cond}: {correct}/{len(verdicts)} correct -- {verdicts}")
