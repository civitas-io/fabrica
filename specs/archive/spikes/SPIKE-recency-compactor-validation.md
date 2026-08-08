# SPIKE: Does `RecencyCompactor`'s strategy actually preserve what matters?

**Status:** Complete · **Timebox:** ~1 hour · **Script:** [scripts/spike-recency-compactor-validation/spike.py](scripts/spike-recency-compactor-validation/spike.py)

---

## Locked question

`RecencyCompactor` (design: [../../../docs/memory.md](../../../docs/memory.md), contract:
[../../../docs/contracts/memory.md](../../../docs/contracts/memory.md)) was the one component
in this entire project with **zero empirical validation** — `preserve_last_n=6`
was a guess, and the whole "preserve recent verbatim + summarize the rest"
strategy had never been tested against the failure mode it exists to prevent:
losing a fact stated early in a conversation that a later decision depends on.

**Question:** does `RecencyCompactor`'s real strategy (last N verbatim + an
LLM-generated summary of everything older) preserve a critical early fact well
enough for a model to reason correctly about it later — compared to (a) full,
uncompacted history, and (b) naive truncation (last N only, summary dropped
entirely)?

## Method

A 13-turn synthetic trip-planning conversation. A hard, non-repeated
constraint (*"our hard budget ceiling is $2400 total... non-negotiable"*) is
stated once, in turn 2. Nine turns of realistic, relevant filler follow
(dates, neighborhoods, attractions, food). The final turn asks the model to
choose between two hotel options ($1400 vs $850) — the *only* way to answer
correctly is to reason from the buried $2400 figure against estimated flight
costs, not just compare the two hotel prices in isolation.

Three conditions, `preserve_last_n=6` for both (b) and (c), 5 runs each, real
Gemini 2.5 Flash calls via Vertex AI (not simulated):

- **(a) FULL** — entire conversation, no compaction.
- **(b) COMPACT** — `RecencyCompactor`'s real strategy: last 6 turns verbatim
  + a genuine LLM call summarizing turns 1–7.
- **(c) TRUNCATE** — last 6 turns only, the summary step skipped entirely.

## A methodology correction made mid-spike, documented rather than hidden

The first version of the correctness checker accepted generic budget language
(*"a great value," "extra room in the budget"*) as evidence the constraint
survived. Checking one `TRUNCATE` answer by hand caught this: it used the word
"budget" several times **while having zero access to the real $2400 figure**
— the checker was rewarding coincidence, not grounding. Corrected the checker
to require the **literal figure (`2400`)** appear in the answer, since that
number exists nowhere in the conversation except the one turn being tested.
This is exactly the kind of self-caught error this project has tried to
surface rather than paper over throughout (`architecture.md §1a`'s citing
"admit the mistake, don't just fix it quietly" pattern, applied here to a
spike's own methodology).

## Result

| Condition | Runs | Grounded-correct | Right answer, wrong/no reasoning |
|---|---|---|---|
| FULL (baseline) | 5 | **5/5** | 0/5 |
| COMPACT (`RecencyCompactor`'s real strategy) | 5 | **5/5** | 0/5 |
| TRUNCATE (naive, no summary) | 5 | **0/5** | 5/5 |

`COMPACT` matched the full-history baseline exactly across every run.
`TRUNCATE` landed on the *same recommendation* every single time — but never
once cited the real constraint, because it genuinely didn't have it anymore.
It got the "right" answer only because Option B happened to also be the
cheaper dollar figure in this scenario, coincidentally aligning with the
correct reasoning without any actual awareness of the $2400 ceiling. A
scenario where the objectively "nicer-sounding" option were the one that
violated the hidden constraint would very likely have broken `TRUNCATE`
outright — this spike didn't need to construct that harder case to make the
point, since the reasoning-transparency gap is already unambiguous from the
literal-figure check.

## What this validates, precisely — and what it doesn't

**Validates:** the core design principle — summarizing what falls out of the
window preserves load-bearing facts that naive truncation silently drops,
even when both strategies keep the exact same verbatim window size. This is
the first real evidence behind `Compactor`'s design, where before there was
none.

**Does NOT validate:** `preserve_last_n=6` specifically as a good default —
this spike held N constant at 6 for both compared conditions to isolate the
summarize-vs-truncate variable cleanly; it says nothing about whether 6 is
too large, too small, or right for real conversation shapes. Also doesn't
test: multiple competing/conflicting facts needing preservation, a summary
itself losing a fact under real token pressure (this scenario's summary had
plenty of room), or behavior right at a hard `budget_tokens` boundary.

## Update to `contracts/memory.md`

The claim *"unlike nearly everything else in this design, compaction has
zero empirical evidence behind it yet"* is no longer accurate for the
core strategy — it now has real, decisive evidence. `preserve_last_n=6`'s
specific value remains unvalidated and should still be flagged as a guess,
not the whole mechanism.
