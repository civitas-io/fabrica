# SPIKE: Does a cheap, non-LLM validation gate on `RecencyCompactor` actually catch and recover from a real compaction failure?

**Status:** Complete · **Timebox:** ~2 hours · **Script:**
[scripts/spike-recency-compactor-validation-gate/spike.py](scripts/spike-recency-compactor-validation-gate/spike.py)

---

## Why this spike, now

Triggered by a real, external bug report and a 5-advisor LLM council (peer-reviewed, 5/5
independent convergence) run on "what's the true path to SOTA memory management" — see
`civitas-io/context`'s `roadmap.md`. The council's unanimous finding: `RecencyCompactor`
(design: [../../docs/memory.md](../../docs/memory.md), contract:
[../../docs/contracts/memory.md](../../docs/contracts/memory.md)) has **zero validation** on its
summary step — it calls the injected `Summarizer` once and trusts the result unconditionally. A
cited SOTA survey (arXiv:2607.21503) documents a real case where unvalidated compaction dropped
task accuracy from 66.7% to 57.1%, *below* the no-context baseline. The council explicitly named
what both of `RecencyCompactor`'s prior two spikes left untested: multiple competing facts, and a
summary that actually fails under real token pressure (`SPIKE-recency-compactor-validation.md`'s
own scenario "had plenty of room" and never once failed).

## Locked question

Does a cheap, non-LLM validation heuristic reliably detect a `RecencyCompactor` summary that has
silently dropped a critical fact, and does one retry (a stricter Summarizer prompt + a larger
budget) actually recover it — compared to today's real, shipped, unvalidated behavior?

## Method

Extended `SPIKE-recency-compactor-validation.md`'s trip-planning scenario to **three** competing
hard facts, in three different domains (so a heuristic keyed to only one signal type can't pass
by accident), all placed outside `preserve_last_n`'s verbatim window:

1. Budget ceiling: "$2400 total, non-negotiable" (numeric)
2. Severe shellfish allergy, carries an EpiPen (safety-critical; load-bearing word "shellfish" is
   a plain content word, not a number or proper noun)
3. Hard return-by date: May 9th, for a graduation (numeric)

**The key methodology correction that made this spike informative, not just decorative**: the
first attempt reused the prior two spikes' own summarization prompt ("preserving every concrete
fact, number, and constraint mentioned...") — and the naive path never failed (5/5 correct, avg
validation score 0.65), because that prompt is already fact-aware. `RecencyCompactor` never
prescribes what its injected `Summarizer`'s prompt says; a real caller can inject anything
satisfying the Protocol, and a generic, careless prompt ("Summarize this conversation in under N
words," no fact-preservation instruction) is the realistic default most callers would reach for
first. Switched the naive `Summarizer` to that generic prompt and tightened `budget_tokens`
further (183 tokens total, vs. 287 raw tokens in the to-be-summarized portion) — this is what
actually let real failures occur, without which the validation gate would have nothing to catch.

Three conditions, real `RecencyCompactor` calls (not reimplemented — same discipline as
`SPIKE-recency-compactor-n-value.md`), real Gemini 2.5 Flash calls via Vertex AI:

- **(a) FULL** — entire conversation, no compaction (baseline), 3 runs.
- **(b) NAIVE** — `RecencyCompactor`'s real, current, shipped behavior: one summarization call
  (generic prompt), no validation, whatever it produces is used, 6 runs.
- **(c) VALIDATED** — the new gate, prototyped in this script: score the naive summary with a
  cheap, non-LLM heuristic (0.6×numeric-token overlap + 0.4×content-word overlap between the
  source turns and the summary, threshold 0.55); below threshold, retry ONCE via a second real
  `RecencyCompactor.compact()` call with a stricter Summarizer prompt ("preserve EVERY numeric
  constraint... EVERY safety/medical constraint... EVERY named entity") and a 1.5× larger budget;
  keep whichever attempt scores higher, 6 runs.

Downstream: a two-part decision question requiring **both** the allergy fact and the date fact to
answer correctly (a shellfish restaurant recommendation and a later return-flight date, both of
which must be rejected) — the real test of "multiple competing facts survive," not just one.

## Result

| Condition | Runs | Both-facts-correct | Avg validation score |
|---|---|---|---|
| FULL (baseline) | 3 | **3/3** | — |
| NAIVE (today's real, shipped behavior) | 6 | **3/6** | 0.315 |
| VALIDATED (new gate) | 6 | **6/6** | 0.674 |

NAIVE failed by dropping the **date** fact specifically in 3/6 runs (the allergy fact survived
every single time — plausibly because "allergy" reads as inherently salient even to a generic
summarizer, while a specific date number does not). All three date-drop failures showed
`missing_numbers` including `'9'` (the day) in the validation detail — the heuristic didn't just
correlate with the failure, it named the exact missing fact. VALIDATED retried on 6/6 runs (the
generic naive prompt almost always scored below threshold) and recovered full correctness on
every single retry, matching the FULL baseline exactly.

## Confirmatory run: same `budget_tokens` on retry, not the spike's 1.5× bump

The result above gave the retry attempt 1.5× more budget than the first attempt, conflating two
variables (a stricter prompt AND more room). Production should not silently exceed a caller's
real `budget_tokens` ceiling on retry — a caller who set that number likely did so for a real
reason (fitting a model's context window). Re-ran VALIDATED with the retry using the exact SAME
`budget_tokens` as the first attempt, isolating the prompt-only effect: **6/6 both-facts-correct**
still held (avg score 0.576). Two of the six runs scored 0.42 post-retry — below the 0.55
threshold — because a few incidental numbers (the $600-900 flight estimate, not one of the two
graded critical facts) didn't survive either attempt; both runs still got the graded facts right,
but a real deployment would correctly see `degraded=True` on those two, an honest signal that
recovery wasn't fully clean even though the specific facts tested here happened to survive. This
is the configuration implemented in production (see below) — same-budget retry, not a budget bump.

## What this validates, precisely — and what it doesn't

**Validates:** the core mechanism the council recommended. A cheap, non-LLM, zero-infra,
zero-additional-LLM-call-on-the-happy-path validation heuristic (i) reliably distinguishes a
summary that dropped a hard-constraint number from one that didn't (0.315 vs. 0.674 average
score, a wide, unambiguous gap — not a marginal one), and (ii) a single retry with a stricter
prompt is sufficient to recover full correctness in every case tested. This directly answers the
council's own flagged blind spot #1 (no concrete mechanism was specified) and #4 (no evidence the
fix actually works, only that it should).

**Does NOT validate:**
- **The retry-exhaustion path.** Every retry in this spike succeeded (score2 ≥ score1 in 6/6
  cases) — this scenario never tested what happens when the stricter retry *also* fails
  validation. `RecencyCompactor`'s real implementation (below) makes an explicit, honest choice
  here, but it is not empirically tested by this spike.
- **The validator-validates-the-validator concern the peer review raised.** This heuristic is
  deliberately NOT another LLM call — it's regex-based numeric/content-word overlap — so it
  doesn't reintroduce the same untrusted-inference risk one level up. But its own false-positive/
  false-negative rate (does it ever wrongly pass a bad summary, or wrongly fail a good one) is
  untested beyond this one scenario.
- **The exact threshold (0.55) or weighting (0.6/0.4).** Chosen as a reasonable starting point
  before this spike ran, not tuned against real data — the gap between NAIVE's 0.315 and
  VALIDATED's 0.674 is wide enough that the exact threshold value likely doesn't matter much in
  *this* scenario, but that has not been stress-tested against a threshold-boundary case the way
  `SPIKE-recency-compactor-n-value.md` stress-tested `preserve_last_n` against a real budget
  boundary.
- **Cost/latency**: this spike does not measure the real cost or latency impact of a
  sometimes-two-call compaction path — a real, deferred follow-up.

## Update to `contracts/memory.md`

`RecencyCompactor` is no longer "the one component with zero validation of its own output" — see
the real implementation added in the same pass as this spike (`fabrica/memory/compactor.py`'s
`ValidatedRecencyCompactor` / the loss-check function), and `CompactionResult`'s new
`validation_score`/`degraded` fields.
