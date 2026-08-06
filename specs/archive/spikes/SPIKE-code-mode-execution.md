# Spike: code-mode execution — the actual headline mechanism

## Question

Given a small set of tools exposed as Python function stubs (not JSON schemas)
plus a `run_code` tool, does a real model write correct code on its first
attempt that calls multiple tools, filters/aggregates data across them, and
returns only a final small result — executed in a real Tier-0 sandbox — with
lower total token cost than the equivalent traditional direct tool-calling
loop, where the same task forces intermediate results through the model's
context across multiple turns?

Directly answers the gap named as most consequential in
[docs/critique.md §B](../../docs/critique.md#b-the-gap-that-matters-more-than-any-single-number):
every prior tool-related spike tested the `find_tools` fallback or the
isolation substrate; none tested the actual headline mechanism
(`tool-execution.md`'s "tools-as-code + sandboxed execution"). Deliberately
scoped to exclude Civitas supervision — that's mature, low-risk, buildable
infrastructure; this spike isolates the one genuinely unknown risk: does the
LLM-writes-code mechanism work at all.

## Result

**Answered — decisively, and with a stronger, more important finding than
the one asked for.**

## Findings

**Task:** *"Find all Fabrica design docs that mention `SKILL.md`, and report
the total word count across just those files."* Ground truth independently
verified: **6 of 10 docs, 6,854 words total.** Chosen because it cannot be
answered from one tool call — it needs list → filter by content → read
matches → sum, exactly the shape where intermediate data normally floods a
model's context.

Both approaches used the *same* underlying real tool implementations
(`list_docs()`, `read_doc(name)` operating on this actual repo's `docs/`
folder) — only the exposure/execution mechanism differed.

### Token cost — the original question, answered

Three independent runs (LLM output is stochastic; one run alone wasn't
enough to trust):

| Run | Approach A (traditional) | Approach B (code-mode) | Reduction |
|---|---:|---:|---:|
| 1 | 23,592 | 4,826 | 79.5% |
| 2 | 23,457 | 4,869 | 79.2% |
| 3 | 23,392 | 4,904 | 79.0% |

**Remarkably consistent — ~79% reduction every time.** Lower than the
~98.7% figure cited from Anthropic/Cloudflare in `landscape.md §1`, which is
expected: that figure is for large-scale enterprise tool registries; this
spike's task is small (10 docs). The *direction and magnitude* of the effect
is real and reproducible, not a cherry-picked result.

### Correctness — the finding nobody asked for, and the more important one

This is the headline result. Across the same 3 runs:

| Run | Approach A (traditional) result | Approach B (code-mode) result |
|---|---|---|
| 1 | **Wrong**: 5,776 words (−15.7%), and visibly second-guessed itself mid-answer about which files even matched | **Exact**: 6,854 words, 6 files |
| 2 | **Wrong**: 4,082 words (−40.5%) | **Exact**: 6,854 words, 6 files |
| 3 | **Wrong**: 3,590 words (−47.6%), **and got the file count wrong** (said 5 docs, not 6) | **Exact**: 6,854 words, 6 files |

**Code-mode was exactly correct in 3 out of 3 runs. The traditional approach
was wrong in 3 out of 3 runs**, with errors ranging from 16% to 48% and, once,
an outright wrong file count. The traditional approach fails because the
model does word-counting by eyeballing text it read into its own context —
an estimation task, not a computation. Code-mode fails to have this problem
because the counting happens as `len(content.split())` in actual Python,
inside the sandbox, where arithmetic is arithmetic rather than an LLM's
approximation of arithmetic.

### First-try code correctness

Inspected directly (not just inferred from token counts): the model's
solving code ran with **zero runtime errors** across every attempt observed.
The "3 code attempts" figure in the automated run is not retries from
failure — direct inspection showed a natural two-step pattern: an
exploratory `list_docs()` call first, then a complete, correct
filter-read-count-sum solution in a single subsequent attempt. This
repeated (with minor variation — sometimes 2 code calls, sometimes 3) across
every run, but the *solving* code itself never needed a bug-fix retry in any
run observed.

## Evidence

Script: `specs/archive/spikes/scripts/spike-code-mode-execution/spike.py`
(held). Both approaches use real Claude-on-Vertex calls, real file I/O on
this actual repository, and real subprocess execution — no mocked data
anywhere in this spike.

```
Ground truth: 6 docs, 6854 words

=== Approach A: traditional direct tool-calling ===
turns: 3
input tokens: 22464  output tokens: 1128  TOTAL: 23592
[wrong final answer, see above]

=== Approach B: code-mode execution ===
turns: 4  code attempts (incl. any retries): 3
input tokens: 4227  output tokens: 599  TOTAL: 4826
[exactly correct: 6,854 words, 6 files]

=== Comparison ===
Reduction: 79.5%
```

Direct inspection of generated code (turn-by-turn), confirming no
error-driven retries:

```python
# Turn 1 (exploration)
docs = list_docs()
print(docs)

# Turn 2 (the actual solve — ran correctly first try)
docs = list_docs()
matching_files = []
word_counts = {}
for doc in docs:
    content = read_doc(doc)
    if 'SKILL.md' in content:
        word_count = len(content.split())
        matching_files.append(doc)
        word_counts[doc] = word_count
print("Files mentioning 'SKILL.md':")
for f in matching_files:
    print(f"  {f}: {word_counts[f]} words")
total = sum(word_counts.values())
print(f"\nTotal word count across matching files: {total}")
# stdout: ...Total word count across matching files: 6854
# returncode: 0
```

## Implications for the plan

- **The headline mechanism works.** `docs/critique.md §B`'s central concern —
  that the differentiated part of the product was the least-validated part —
  is resolved. Code-mode is not just a plausible design anymore; it produced
  correct results with lower cost, repeatedly, against a real task on real
  data.
- **The correctness finding may be a bigger pitch than the token-savings
  finding.** `tool-execution.md`'s "why Fabrica" section leads with token
  economics. This spike suggests **reliability** — code does exact
  computation, models approximate — may be an equally or more compelling
  argument for tasks involving counting, filtering, aggregation, or anything
  arithmetic. Worth considering as a named part of the pitch, not just a
  side effect of token savings.
- **This validates `tool-execution.md`'s core interface shape** —
  `ToolNamespace.stubs()`/`.open()`/`.call()` and binding into a `Sandbox` —
  without needing to change it. The spike's simplified version (function
  stubs + a `run_code` tool) is a faithful minimal proof of that same shape.
- **Civitas supervision remains correctly out of scope for this spike** (per
  the earlier discussion) — this proves the mechanism is worth wrapping in
  supervision, it doesn't test the wrapping itself.

## What was NOT explored

- **Civitas-supervised execution** — deliberately excluded from this spike's
  scope; a natural, lower-risk follow-up now that the mechanism itself is
  validated.
- **Real isolation tiers** (gVisor/Firecracker/srt) — this spike used Tier 0
  (bare subprocess) on purpose, to isolate the code-writing question from the
  isolation question (both already validated separately in prior spikes).
- **Adversarial/malicious code** — this task had no reason for the model to
  write anything dangerous. Whether code-mode holds up against a model asked
  to do something it shouldn't, or against genuinely malicious generated
  code, is untested — this is what the `Sandbox` isolation tiers exist for,
  but the *interaction* between "model writes surprising code" and "sandbox
  contains it" wasn't stressed here.
- **Larger tool namespaces** — only 2 functions were exposed. Whether the
  `stubs()`/`.open()` progressive-disclosure pattern holds correctness and
  cost benefits at realistic scale (dozens of tools) is untested.
- **Tasks requiring many more tool calls per solve** — this task needed
  exactly 2 real calls (list, then a single comprehensive solve). Whether
  code-mode holds its advantage on tasks needing many more distinct calls or
  more complex control flow (loops with side effects, retries, branching on
  intermediate results) wasn't tested.
- **A rigorous sample size** — 3 runs is enough to trust the direction and
  rough magnitude of both findings, not enough for a statistically rigorous
  claim.

## Recommendation

**Proceed with code-mode as P1's actual headline, not just a de-risked
fallback.** The mechanism works, is reproducibly cheaper, and — unexpectedly
— reproducibly *more correct* than the traditional alternative for
computation-shaped tasks. Before `plan-work`:

1. Consider elevating the correctness/reliability finding into
   `tool-execution.md`'s stated differentiation, not just token economics.
2. The natural next spike (not this one) is Civitas-supervised execution of
   this same mechanism — now justified, since the mechanism itself is proven
   worth wrapping.
3. Adversarial robustness and larger-namespace behavior remain real,
   unresolved risks — smaller than "does this work at all," but real before
   a production claim.
