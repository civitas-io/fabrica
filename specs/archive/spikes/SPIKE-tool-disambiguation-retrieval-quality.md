# Spike: tool disambiguation / retrieval quality (+ prx reuse detour)

## Question

Given a set of deliberately overlapping/confusable tool descriptions, does simple
keyword-overlap matching achieve reasonable top-3 retrieval precision across a
benchmark of test queries — including queries that paraphrase rather than reuse the
tool's own words — or does it fail specifically on semantic-but-not-lexical
matches? And does a lightweight embedding baseline meaningfully outperform it?

**Detour, at explicit request before going deep:** investigate `prx` (Praxis) —
read its README + `skills/agents.md` — and check whether its existing embedded
semantic-search engine has reusable value for tool search, before building
anything from scratch.

Tests the cross-cutting disambiguation open question flagged in
[docs/skills-gateway.md](../../docs/skills-gateway.md#open-questions) and
[docs/tool-execution.md](../../docs/tool-execution.md#open-questions).

## Result

**Answered** — and the prx detour changed the shape of the answer.

## Findings

### Part 1 — the core question

12 deliberately confusable tools across 4 clusters (messaging x3, query x3,
scheduling x2, 4 single-instance distractors), 12 benchmark queries (6 "easy" —
share a distinctive word with the tool's own text; 6 "hard" — paraphrased, no
lexical overlap), one unambiguous ground-truth tool per query.

| Method | precision@3 | top-1 | hard-only precision@3 |
|---|---:|---:|---:|
| Naive keyword (Spike-1-style word overlap) | 8/12 (67%) | 7/12 (58%) | 5/7 (71%) |
| prx default (fused literal+semantic+structural, RRF) | 12/12 (100%) | 11/12 (92%) | 7/7 (100%) |
| prx `--semantic` (isolated embedded model, no literal) | 12/12 (100%) | 11/12 (92%) | 7/7 (100%) |

**Keyword matching fails exactly where hypothesized** — e.g. missed
`search_documents` for "find relevant docs about the new feature" (no shared
distinctive word), and missed `translate_text` for "convert this sentence into
spanish" entirely (0 results — no word overlap at all). It even missed one *easy*
case (`get_weather` for "what's the temperature in nyc" — "temperature"/"nyc"
don't appear in the tool's own text).

**Embedding-based retrieval meaningfully outperforms it** — 100% vs 67% overall,
and the gap is concentrated exactly on the hard/paraphrase cases (100% vs 71%) as
predicted. Confirms embeddings are not optional for the "used only when relevant"
half of Devon's JTBD once the tool/skill pool has any genuine ambiguity in it.

### Part 2 — the prx detour (unplanned, but decisive for architecture)

Two rounds of testing prx's actual embedded model against **short, non-code,
business-API-style text** — a domain it was never built or tuned for:

1. **First pass (5 tools, eyeballing raw scores):** looked weak. Relevance scores
   were low and tightly clustered (0.01–0.02), nowhere near the README's own
   code-search example (0.94). Initial read: "doesn't transfer well."
2. **Second pass (12 tools, proper rank-based precision@3 benchmark):** the
   *ranking* was excellent — 100% precision@3, 92% top-1 — even though the
   absolute relevance scores stayed just as low (0.01–0.04) on the correct hits.

**The first pass was a methodology error, not a real finding.** Absolute
relevance scores calibrated for code search do not transfer to this domain, but
they were never the signal that mattered — **rank order** was reliable throughout.
This is a real, actionable engineering constraint: anything built on top of prx's
model for this use case must select by **top-k rank**, never by an absolute
relevance threshold (a rule like "only count matches above 0.5" would have
silently discarded every correct answer in this benchmark).

## Evidence

Script + fixture generator: `specs/archive/spikes/scripts/spike-tool-disambiguation-retrieval-quality/spike.py`
(held, not deleted, per instruction). Full per-query pass/fail output captured
in the script's run log above this doc in the session transcript.

Sanity check that prx works as documented: `--literal "Teams"` returned an exact
match at `relevance: 1.0`; `--literal` on a paraphrased query returned zero
matches (`total_matches: 0`) — confirming literal mode behaves exactly as
expected and isolating the semantic contribution cleanly.

## Implications for the plan

- **`tool-execution.md`'s "build vs. wrap" stance needs a third option added.**
  Currently it names LlamaIndex/LangChain as embedding-backend adapters to wrap.
  **prx should be added as a candidate backend** — Apache 2.0 (no license
  conflict), zero-dependency static binary, no vector DB or model server, and
  empirically strong on this exact task despite being tuned for a different
  domain.
- **This does NOT mean "extend prx itself with a tool-search command."** That
  would blur prx's own scope — its consumer is a human/coding-agent at a
  terminal, not a model mid-inference inside a governed Civitas process (the
  exact distinction already codified in
  [tool-execution.md](../../docs/tool-execution.md#why-this-lives-in-fabrica-not-the-rust-toolchain-prxtessera)).
  The right shape is **Fabrica wrapping prx's engine as one backend behind its
  own `find_tools` interface**, not prx growing a new persona.
- **Rank-based selection, not threshold-based, is now a hard requirement** for
  any embedding backend Fabrica adopts — not just prx. Worth stating explicitly
  in `tool-execution.md`'s `ToolIndex` interface.
- Given prx already ships `scripts/distill_model.py` (their own Model2Vec
  distillation pipeline), there's a **second, un-tested path**: distill a small
  model specifically for tool/API-description text, rather than reusing the
  code-tuned one as-is. This spike suggests that may not even be necessary —
  the off-the-shelf model already performs well — but it's the natural next
  step if quality degrades at larger scale.

## What was NOT explored

- **Integration mechanism.** How Fabrica would actually invoke prx's model was
  not tested: shelling out to the compiled binary per call (simplest, but
  process-spawn overhead on a hot path), running `prx mcp` as a persistent
  server and calling it over MCP, or extracting the raw `potion-retrieval-32M`
  safetensors weights and loading them directly in Python (no subprocess, but
  reimplements prx's inference path). This is a real architecture decision for
  `plan-work`, not this spike.
- **Scale.** Only 12 tools tested. Research and `tool-execution.md` both discuss
  100–200+ tool enterprise registries; whether precision holds at that scale is
  unknown.
- **Live index updates.** prx's index is filesystem-based and rebuilt via
  `prx index`. Whether it supports the live register/deregister behavior
  `tool-execution.md` requires for a dynamic tool registry (Q5 in the original
  Fabrica design doc) was not tested here.
- **Latency.** Only retrieval quality was measured, not per-call latency of
  invoking prx (relevant given this would sit in the hot path of every
  tool-augmented turn).
- **True near-duplicate disambiguation at scale.** Only one genuinely similar
  pair existed in this benchmark (`query_analytics_db` vs `query_customer_records`)
  and it resolved correctly — but that's one data point, not a stress test.

## Recommendation

**Proceed with embeddings as a first-class retrieval backend — the "keyword-only
might be enough" hypothesis is rejected.** Additionally: **add prx as a candidate
embedding backend for Fabrica's `find_tools`**, alongside LlamaIndex/LangChain
adapters already named in `tool-execution.md`, given it's zero-dependency,
same-org, Apache 2.0, and empirically strong even outside its trained domain.
Before `plan-work`:

1. Update `tool-execution.md`'s backend list to include prx, and add the
   rank-not-threshold requirement to the `ToolIndex` interface sketch.
2. Treat "how to invoke prx from Fabrica" (subprocess / MCP / direct model
   extraction) as its own follow-up spike if this direction is pursued — it's an
   architecture decision, not a detail to guess at now.
3. Do not extend prx itself with tool-search capabilities — the persona/consumer
   boundary already settled in `tool-execution.md` holds; reuse the *engine*
   inside Fabrica's own interface instead.
