# Spike: tool-retrieval token overhead

## Question

Can a `find_tools`-style two-turn retrieval keep schema-token overhead bounded
(roughly O(1)) as registered tool count N grows from 5 to 50, when measured
against **real** Claude-on-Vertex token usage — versus sending all N schemas
upfront?

Tests Priya's "flat index cost" success metric in
[docs/problem-definition.md](../../docs/problem-definition.md).

## Result

**Answered.**

## Findings

Measured `usage.input_tokens` from real Vertex calls (`claude-sonnet-4-6@default`),
comparing two approaches at N = 5, 20, 50 registered tool schemas:

| N tools | Static (all N upfront) | `find_tools` total (2 turns) | Turn 1 (find_tools only) | Turn 2 (matched schemas only) |
|---:|---:|---:|---:|---:|
| 5 | 1,211 | 1,848 | 730 | 1,118 |
| 20 | 3,099 | 1,857 | 730 | 1,127 |
| 50 | 6,963 | 1,953 | 730 | 1,223 |

- **Static scales linearly with N**, as expected: ~128 tokens/tool
  (`(6963-1211)/(50-5)`), consistent with the ~"few hundred tokens per schema"
  figures cited in `landscape.md`.
- **`find_tools` stays effectively flat**: only **+105 tokens** total (1,848 → 1,953)
  across a **10x growth in N** (5 → 50), versus **+5,752 tokens** for static over
  the same range.
- **Turn 1 is exactly constant** (730 tokens at every N) — it only ever sees the
  single `find_tools` meta-schema, fully independent of registry size. This is the
  cleanest confirmation of the thesis.
- **Turn 2 grows very slightly** (1,118 → 1,223) even though the matched-tool limit
  is fixed at 3. Root cause identified: the spike's synthetic schema pool cycles
  through 10 domains, so at higher N the keyword matcher occasionally picks a
  different *variant* tool (e.g. `send_email_v2` instead of `send_email`) with a
  marginally different description. This is an artifact of the spike's synthetic
  data generator, not evidence against the architecture — the *number* of schemas
  returned to turn 2 never exceeds the configured limit (3) regardless of N.
- At N=50, `find_tools` used **~28% of static's input tokens** (1,953 vs 6,963) for
  a single query needing exactly one tool. The gap widens as N grows further (the
  static line keeps climbing; the find_tools line is nearly horizontal) — so real
  registries with 100+ tools should show an even larger relative gain than this
  spike's N=50 ceiling shows.

## Evidence

Script output (see holding pen, not deleted per instruction):
`specs/archive/spikes/scripts/spike-tool-retrieval-token-overhead/spike.py`

```
 N tools |  static (O(N)) |  find_tools total |    turn1 |    turn2
----------------------------------------------------------------------
       5 |           1211 |              1848 |      730 |     1118
      20 |           3099 |              1857 |      730 |     1127
      50 |           6963 |              1953 |      730 |     1223
```

Method: real `POST .../publishers/anthropic/models/claude-sonnet-4-6@default:streamRawPredict`
calls against the `fdl-c-gemini-apis` Vertex project, `tool_choice` forced for
determinism, `usage.input_tokens` read directly from the API response (not
estimated/tokenizer-approximated).

## Implications for the plan

- **The core thesis holds under real measurement, not just cited research.**
  Priya's "flat index cost" success metric is achievable with the simplest
  possible implementation — a forced two-turn tool-call sequence and a trivial
  keyword matcher. No embedding backend was needed to see the effect.
- **Turn 1's constant cost (730 tokens) is the true fixed overhead of adopting
  `find_tools` at all** — worth surfacing to users directly (e.g. in docs) as "this
  is what you pay regardless of registry size."
- The turn-2 variance suggests the `ToolSchema`/matching layer needs **stable,
  deterministic identity** for near-duplicate tools (avoid a matcher that
  flip-flops between variants for the same underlying capability) — a concrete
  requirement to carry into `plan-work`, not previously called out in
  `tool-execution.md`.
- Cost delta is measurable in dollars, not just tokens: at Sonnet 4.6 Vertex
  pricing ($3/1M input), the N=50 case is $0.021 (static) vs $0.006 (find_tools)
  per query — small per-call, but this recurs on every tool-augmented turn in
  production, so it compounds exactly as `tool-execution.md` argues.

## What was NOT explored

- **Real retrieval quality** — keyword matching here was a one-line trivial
  overlap check, not BM25 or embeddings. This spike measured *token shape*, not
  *retrieval correctness*. That's Spike 4 (disambiguation), still pending.
- **N beyond 50** — research cites 100-200+ tool enterprise registries; this spike
  stopped at 50 for time. The trend strongly suggests the gap only widens, but
  wasn't measured past N=50.
- **Session caching** (RFC 0001's proposed mechanism to skip redundant `find_tools`
  calls within a session) — not implemented or measured here; each N was a fresh
  two-turn sequence.
- **Multiple tools needed per task** — this spike used a single-tool-need query.
  Tasks needing 3+ distinct tools (multiple `find_tools` round-trips) were not
  measured, though `tool-execution.md` already acknowledges this case is more
  expensive than static for very high tool-counts-per-task.
- **Real MCP source integration** — schemas were synthetic/local, not pulled from
  an actual MCP server.
- **Latency** — only token cost was measured, not the added round-trip latency of
  the second turn.

## Recommendation

**Proceed with the `find_tools` two-turn design as specified.** The core token-flat
claim is real, not just cited from secondary research. Before `plan-work`:

1. Carry the "stable tool identity" requirement (from the turn-2 variance finding)
   into the `ToolSchema`/matcher design in `tool-execution.md`.
2. Spike 4 (disambiguation/retrieval quality) is now higher priority than before —
   this spike proved the *token* half of the thesis; retrieval *correctness* is the
   remaining unproven half, and per `skills-gateway.md`'s open question, is the
   same failure mode that will affect skills and memory search too.
3. Session-caching and N>50 remain open for a future spike or can be deferred to
   implementation-time benchmarking once real code exists.
