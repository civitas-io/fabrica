# Spike: does Mem0 actually wrap cleanly behind MemoryStore?

## Question

Can Fabrica's `MemoryStore` protocol (`write`/`search`/`get`/`forget`, `Scope`-based)
actually wrap Mem0 cleanly, or does real integration friction emerge — mismatched
method signatures, a scope-model mismatch, or a dependency footprint heavier than
`memory.md`'s "wrap, don't build" framing implies? Does a basic write-then-search
round trip actually work end to end?

Closes the last remaining gap named in [critique.md](../../../docs/critique.md): P4
(memory) had zero empirical work of any kind before this spike.

## Result

**Answered.** The round trip works end to end — but "wrap, don't build" is not
the same as "wrap, zero config," and real API friction exists inside Mem0 itself,
independent of anything Fabrica does.

## Findings

### Finding 1 — Mem0 cannot instantiate at all without an OpenAI API key, by default

```python
from mem0 import Memory
Memory()
# OpenAIError: Missing credentials. Please pass an `api_key`... or set
# OPENAI_API_KEY or OPENAI_ADMIN_KEY
```

The default embedder is OpenAI. This directly contradicts an implicit assumption
in `memory.md`'s "wrap, don't build" framing — wrapping an existing library does
not automatically mean a zero-infrastructure default. Priya's "no external
services" bar (from `problem-definition.md`) is **not met by Mem0's own defaults.**

### Finding 2 — A fully local configuration exists, but is not zero-effort

Mem0 supports 11 embedder providers and 23 vector store providers, including fully
local options (`fastembed` — local ONNX embeddings, no API key; `chroma` — local
vector store). Configuring this explicitly **does** work:

```python
config = {
    "embedder": {"provider": "fastembed", "config": {"model": "BAAI/bge-small-en-v1.5"}},
    "vector_store": {"provider": "chroma", "config": {"collection_name": "...", "path": "..."}},
    "llm": {"provider": "openai", "config": {"api_key": "unused-if-infer-false"}},
}
Memory.from_config(config)  # works
```

Two costs worth naming: (a) this requires real configuration knowledge a "wrap it
and go" story implies you shouldn't need, and (b) the local embedding model
download took **~35 seconds** on first use (one-time, HuggingFace Hub fetch) — a
real, if small, cold-start cost analogous to Firecracker's snapshot-creation cost
found in an earlier spike. Note also: an `llm` config block is **required to
instantiate at all**, even when `infer=False` means it's never actually called at
write time — a real, if minor, API awkwardness.

### Finding 3 — `add()` and `search()` have inconsistent parameter conventions, within Mem0 itself

```python
mem.add(content, user_id=..., agent_id=..., run_id=...)      # top-level kwargs — works
mem.search(query, user_id=..., agent_id=..., run_id=...)     # same kwargs — REJECTED
# ValueError: Top-level entity parameters frozenset({'agent_id', 'user_id', 'run_id'})
# are not supported in search(). Use filters={'user_id': '...'} instead.
```

This is a real inconsistency inside Mem0's own API surface, not a Fabrica design
problem — but it's exactly the kind of friction a thin `MemoryStore` wrapper has
to absorb silently so Fabrica's own interface stays consistent across `write` and
`search`, which it does (see the working adapter below).

### Finding 4 — Fabrica's `team_id` scope dimension has no native home in Mem0, but the workaround is clean

Mem0 natively supports `user_id`/`agent_id`/`run_id` (its analog to `session_id`).
It has **no `team_id` concept at all.** Folding it into `metadata` works and
survives a full write→search round trip, confirmed directly:

```python
# write:  metadata={"team_id": "team1"}
# search result:
{'metadata': {'team_id': 'team1'}, 'score': 0.857, 'user_id': 'u1',
 'agent_id': 'agent1', 'run_id': 's1', ...}
```

### Finding 5 — semantic search works locally, with a real relevance score

Query *"what theme does the user prefer?"* against stored content *"The user
prefers dark mode..."* (no lexical overlap on the key term "theme"/"dark mode")
returned a match at **score 0.857** — local `fastembed` embeddings, no OpenAI
call, no `infer=True` LLM call. Consistent with the disambiguation-quality
findings from the tool/skill retrieval spikes: embeddings generalize past exact
wording even in a fully local configuration.

### Finding 6 — `infer=False` skips LLM extraction, but also skips deduplication

Running the write step twice (across separate script invocations, same persisted
Chroma path) produced **two separate memory entries** with identical content, not
one updated entry. `infer=True` is what normally drives Mem0's semantic
dedup/update logic via an LLM comparison step; `infer=False` (needed to avoid
requiring an LLM at all) trades that away. Worth knowing before assuming
`infer=False` is a strictly-better default for a zero-infra path — it is
zero-infra, but it is also "write-only," not "write-or-update."

## Evidence

Script: `specs/archive/spikes/scripts/spike-memory-mem0-wrap/spike.py` (held).
Environment quirk worth recording: this machine's `pip3` is tied to a Python 3.9
interpreter separate from `python3` on `PATH` (3.14) — the spike had to be run
with `/usr/bin/python3` explicitly. Not a Mem0 finding, but a reminder that "pip
install" and "which Python actually has it" are different questions on a dev
machine with multiple Pythons.

```
=== Attempt 1: Memory() with zero config ===
FAILED: OpenAIError: Missing credentials...

=== Attempt 2: fully local config (fastembed + chroma, infer=False) ===
Instantiated OK.

=== write() ===
{'results': [{'id': '...', 'memory': 'The user prefers dark mode and lives in Bangalore.', 'event': 'ADD', ...}]}

=== search() ===
{'results': [{..., 'score': 0.8573911190032959, 'metadata': {'team_id': 'team1'}, ...}]}
```

## Implications for the plan

- **`memory.md`'s "wrap, don't build" thesis holds architecturally** — the
  `MemoryStore` protocol maps cleanly onto Mem0's real methods, and a working
  adapter was built in minutes, not hours. But the doc should be corrected to say
  **"wrap, with real configuration required for a zero-infra default"** — not
  implying Mem0 itself ships one.
- **A `fabrica-contrib[mem0]` adapter should ship a pinned, working local config**
  (fastembed + chroma + `infer=False`) as its own default, not expose Mem0's raw
  config surface — Priya should never see the OpenAI-credentials error this spike
  hit first.
- **The `search()`/`add()` parameter inconsistency must be absorbed inside the
  adapter**, invisibly — Fabrica's own `MemoryStore.search(scope, query, limit)`
  signature stays clean regardless of what's happening underneath.
- **`team_id`-via-metadata is a workable, validated pattern** — confirmed to
  survive a real round trip, not just theorized.
- **`infer=False`'s write-only (no dedup) behavior needs a documented tradeoff**,
  not a silent default — worth a line in `memory.md` about what's given up for a
  zero-LLM-dependency write path.

## What was NOT explored

- **Other backends** (Zep, Letta, Cognee, LangMem) — only Mem0 was tested. Each
  may have its own version of these frictions, or different ones entirely.
- **`infer=True` with a real LLM** — this spike deliberately avoided it to test
  the fully-local path; the LLM-driven fact-extraction/dedup behavior (Mem0's
  actual headline feature) was not exercised at all.
- **Async usage** (`AsyncMemory`) — `memory.md`'s protocol is `async def`; this
  spike used the sync `Memory` class only.
- **Scale** — one write, one search. Whether Mem0's local (fastembed + Chroma)
  path holds up with a realistic memory volume per user/session is untested.
- **`get`/`forget`** — implemented in the adapter, not exercised end-to-end in
  this run (time bounded, and `write`/`search` were the higher-risk unknowns).

## Recommendation

**Proceed with Mem0 as the first `fabrica-contrib` memory adapter** — the
protocol fit is real and clean, and every friction point found has a concrete,
already-tested workaround. Before `plan-work`:

1. Ship a pinned local-first config (fastembed + chroma + `infer=False`) as the
   adapter's own default, not Mem0's raw defaults.
2. Document the `infer=False` dedup tradeoff explicitly in `memory.md`.
3. `infer=True` (Mem0's actual smart-memory value proposition) remains untested —
   a real gap if that mode is what Fabrica actually wants to recommend for
   production use over the zero-infra local path.
