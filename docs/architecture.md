# Fabrica: Architecture Walkthrough

**Status:** Design, validated by seven spikes · **Last updated:** 2026-08

This doc is the visual entry point into the design — each section builds on the
last. For the reasoning behind any decision shown here, follow the links into the
component docs; for the evidence behind any number, follow the links into
`specs/archive/spikes/`.

---

## 1. Where Fabrica sits

Three pillars, one platform. Fabrica is the middle layer conceptually (shapes
context) but architecturally sits *between* the model and Civitas, governed by
Presidium at every boundary crossing.

![Where Fabrica sits — three pillars: Context (Fabrica), Control (Presidium), Runtime (Civitas)](assets/platform-layers.svg)

**The one-line version:** Civitas keeps agents alive. Presidium keeps them
accountable. Fabrica decides what they see and how they act on it. Full detail:
[context-layer.md](context-layer.md).

---

## 2. The two ways a model gets a capability

Fabrica offers exactly two paths, deliberately — a headline and a fallback, not a
menu of options. **Both are now validated by spike, not just designed.**

![Two ways a model gets a capability: code-mode headline vs find() fallback, with measured comparison table](assets/two-capability-paths.svg)

Full design: [tool-execution.md](tool-execution.md). Evidence:
[SPIKE-code-mode-execution.md](../specs/archive/spikes/SPIKE-code-mode-execution.md).

---

## 3. Code-mode execution, step by step

![Code-mode execution sequence: Model, Fabrica, Sandbox, real tools — only the final result crosses back into context](assets/code-mode-sequence.svg)

This is the mechanism behind the ~98.7% token reduction Anthropic/Cloudflare
reported (`landscape.md §1`) — Fabrica's version is vendor-neutral and
self-hostable, running on a Civitas-supervised process instead of a
vendor-locked platform.

---

## 4. The shared retrieval engine (tools + skills, unified)

Both the `find()` fallback and skill discovery run on **one** engine, not two —
resolved this way after measurement showed the original separate skill-index
design wasn't actually flat ([SPIKE-skill-progressive-disclosure.md](../specs/archive/spikes/SPIKE-skill-progressive-disclosure.md)).

![Shared Retriever engine: tools and skills as Indexables, KeywordBackend/PrxBackend/LlamaIndex backends, rank-not-threshold](assets/retrieval-engine.svg)

Full design: [retrieval.md](retrieval.md).

---

## 5. Isolation: platform-dispatched, not one technology

The backend is **auto-detected by host OS and hidden from users** — a deliberate
exception to how Civitas normally exposes config (transport selection *is* a
user choice; isolation backend is not).

![Isolation tiers per platform: Linux (gVisor, Firecracker), macOS (srt, libkrun), Windows (deferred)](assets/isolation-tiers.svg)

Full design + the corrected boot-time distinction (VMM-ready vs. actually-usable):
[isolation.md](isolation.md).

---

## 6. Memory: wrap, with a working default — not the raw library

![Memory wrap pattern: MemoryStore protocol over a default local store, the validated Mem0 adapter, and untested alternatives](assets/memory-wrap.svg)

The spike found Mem0's own defaults require an OpenAI API key just to
instantiate — directly contradicting a zero-infra assumption until explicitly
reconfigured. The adapter therefore ships **its own pinned local config**
(fastembed + chroma + `infer=False`), so that friction never reaches a user.
Evidence: [SPIKE-memory-mem0-wrap.md](../specs/archive/spikes/SPIKE-memory-mem0-wrap.md).

---

## 7. Package structure

![Package structure: fabrica core (protocols + Rust-backed defaults) vs fabrica-contrib (opt-in adapters)](assets/package-structure.svg)

**Engineering principle applied throughout:** where Fabrica *builds* compute
internals (like the default `KeywordBackend`), it's Rust with a Python binding —
matching prx's own shape — not pure Python. Wrapped libraries (Mem0, LlamaIndex,
prx itself) are unaffected. Detail: [context-layer.md](context-layer.md#engineering-principle-rust-for-compute-python-for-interface).

---

## 8. How it all closes the loop — one request, start to finish

![One request lifecycle: User, Agent, Presidium, Fabrica, Sandbox — Presidium gates before Fabrica acts](assets/request-lifecycle.svg)

This is the seam map from [civitas-presidium-integration.md](civitas-presidium-integration.md)
made concrete as a single request's lifecycle.

---

## Where to go next

| If you want... | Go to |
|---|---|
| The personas this was designed for | [personas.md](personas.md) |
| Per-persona success metrics | [problem-definition.md](problem-definition.md) |
| Every claim checked against evidence | [critique.md](critique.md) |
| Raw spike data | [`specs/archive/spikes/`](../specs/archive/spikes/) |
