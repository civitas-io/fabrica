# Spike: Does `preserve_last_n`'s value matter, and what happens at a real `budget_tokens` boundary?

**Status:** Complete · **Timebox:** ~1 hour · **Script:** [scripts/spike-recency-compactor-n-value/spike.py](scripts/spike-recency-compactor-n-value/spike.py)

---

## Locked question

[SPIKE-recency-compactor-validation.md](SPIKE-recency-compactor-validation.md) validated
`RecencyCompactor`'s core summarize-vs-truncate strategy (5/5 grounded-correct vs. 0/5),
but explicitly named two things it did **not** test: whether `preserve_last_n=6`
specifically is a good value (it held N constant to isolate the summarize-vs-truncate
variable), and behavior at a real `budget_tokens` boundary ("this scenario's summary
had plenty of room").

**Question:** does varying `preserve_last_n` (2, 6, 10) against a deliberately
**tight** `budget_tokens` — tight enough that the larger N values genuinely get
clipped by the budget, not just by N itself — change whether the model's final
answer still correctly grounds on a buried early constraint?

## Method

Same validated 13-turn trip-planning conversation as the first spike (a hard,
non-repeated \$2400 budget ceiling stated once in turn 2, buried under 9 turns of
realistic filler, then a final decision that can only be answered correctly by
reasoning from that buried figure).

**Unlike the first spike, this one calls the REAL production code directly** —
`fabrica.memory.compactor.RecencyCompactor`/`_select_preserved`, imported from the
actual package, not a hand-rolled reimplementation of the slicing logic. The whole
point this time is whether the real `preserve_last_n`-vs-`budget_tokens` interaction
holds up; reimplementing it by hand would risk testing something subtly different
from the real mechanism.

`Message.tokens` uses a real (if approximate) per-message token estimate
(~4 chars/token) — a genuine stand-in for the model-provider usage reporting
`contracts/memory.md` designed this field around, not a fabricated number tuned to
force a specific outcome.

`budget_tokens` was set to the real sum of the last 6 messages' tokens (278) —
chosen specifically so:

| `preserve_last_n` | Tokens needed | vs. 278-token budget |
|---|---:|---|
| 2 | 119 | fits trivially |
| 6 | 278 | exactly at the boundary |
| 10 | 388 | **exceeds it — genuinely clipped** |

Three `preserve_last_n` values (2, 6, 10), 5 runs each, real Gemini 2.5 Flash calls
via Vertex AI for both the summarization step and the final decision (not
simulated) — `RealGeminiSummarizer` implements the actual `Summarizer` Protocol
`RecencyCompactor` is designed to receive via dependency injection, the same shape
`CivitasBridge` wires in for real.

## Result

The budget-clipping mechanism engaged exactly as coded, not just in theory: with
`preserve_last_n=10` requested against the 278-token budget, `RecencyCompactor`
actually preserved only 6 messages verbatim in every single run — `_select_preserved`
correctly stopped adding messages once the cumulative token count would have
exceeded the budget, silently honoring the tighter of the two constraints rather
than crashing, ignoring the budget, or off-by-one erroring.

| `preserve_last_n` (requested) | Actually preserved (all 5 runs) | Grounded-correct |
|---|---:|---|
| 2 | 2/2 | **5/5** |
| 6 | 6/6 | **5/5** |
| 10 | 6/10 (clipped by budget) | **5/5** |

Every single run, across all three N values, correctly grounded on the buried
\$2400 figure — including the `preserve_last_n=2` condition, where only the final
two conversational turns (the two hotel options themselves) were preserved
verbatim and everything else, including the constraint itself, had to survive
through the LLM-generated summary alone.

## What this validates, precisely — and what it doesn't

**Validates:** the real `preserve_last_n`-vs-`budget_tokens` interaction in
`_select_preserved` works correctly under real contention, not just in the
abstract — this is the first time that code path has been exercised with the two
constraints actually competing, rather than one always dominating. Also: in this
specific scenario, `preserve_last_n`'s exact value (across a 5x range, 2 to 10)
made no observable difference to outcome correctness — the `Summarizer`'s own
quality, already validated as reliable in the first spike, is doing the real
preservation work for a single, clearly-stated hard constraint, not the size of
the verbatim window.

**Does NOT validate:** that `preserve_last_n`'s value never matters. This spike
used one constraint, stated once, in a scenario the first spike already showed
summarization handles well. It says nothing about: multiple competing/conflicting
facts needing preservation simultaneously (where a tighter summary budget might
force the summarizer to drop one of several important details); facts that need
literal, verbatim preservation because paraphrasing loses precision (e.g., an
exact code snippet, a precise technical value a summary might round); or a
`Summarizer` of meaningfully lower quality than Gemini 2.5 Flash. `preserve_last_n`
choice may matter more in those cases — this spike only rules it out for the
single-clear-constraint case both spikes have now tested.

## Update to `contracts/memory.md`

`preserve_last_n=6`'s status changes from "an unvalidated guess" to "validated as
one reasonable value among several (2, 6, 10) that all performed equally well in
the tested scenario" — a real, if scenario-bounded, improvement in confidence, not
a claim that the number has been comprehensively tuned. `contracts/memory.md`'s
open item on this is updated to reflect the narrower remaining gap: multi-fact and
precision-sensitive scenarios, not the single-constraint case.
