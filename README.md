# Fabrica

**The context layer for Python agents.**

Part of the [Civitas](https://github.com/civitas-io/python-civitas) platform.

> **Resuming after a break or a context compaction? Read [`HANDOFF.md`](HANDOFF.md)
> first** — the full arc of decisions, why each was made, and the one open
> question that needs answering before contracts work continues.

> **Status:** Pre-alpha — thesis, design, and validation. Seven spikes
> ([`specs/archive/spikes/`](specs/archive/spikes/)) tested the riskiest claims
> against real hardware and real API calls — see [`docs/critique.md`](docs/critique.md)
> for what held up, what got corrected, and what's still open. Code lands after
> `plan-work` turns this into an implementation plan.

---

## Why Fabrica?

*Fabrica* is the Latin word for **workshop** — the place where raw material is shaped
into something usable. That is exactly this layer's job: shape the raw material of an
agent's world (tools, skills, memory, prompts) into the small, precise set of tokens
that actually enter the model's context window.

The Civitas platform is three pillars:

```
┌──────────────────────────────────────────────┐
│  CONTEXT LAYER   ◄── Fabrica                  │
│  What goes INTO the window: tools, skills,    │
│  memory, prompts — and how it is executed     │
├──────────────────────────────────────────────┤
│  CONTROL LAYER   ◄── Presidium                │
│  What the agent is ALLOWED to do: policy,     │
│  grants, HITL, cost limits                    │
├──────────────────────────────────────────────┤
│  RUNTIME LAYER   ◄── Civitas                  │
│  Keeping the agent ALIVE: supervision,        │
│  message routing, transport, observability    │
└──────────────────────────────────────────────┘
```

Civitas keeps agents alive. Presidium keeps them accountable. **Fabrica decides what
they see and how they act on it.**

---

## The thesis

Modern agents don't fail because the model is weak. They fail because the **context
window is mismanaged**: every tool schema, every skill, every memory, every
intermediate result is stuffed into the prompt whether the model needs it or not.

Two hard limits follow:

1. **Token cost scales with capability.** 50 tools × ~300 tokens/schema = ~15,000
   tokens of overhead *before the user says anything.* Add memory and skills and the
   window fills with noise.
2. **Accuracy collapses.** Tool-selection accuracy degrades past ~20–30 tools; large
   intermediate results crowd out reasoning.

The industry answer in late 2025 was decisive and points the way:

- **Anthropic's Tool Search Tool** (24 Nov 2025) — tools marked `defer_loading` are
  fetched on demand; Anthropic reports **190,000+ tokens preserved.**
- **Code execution with MCP / "Code Mode"** (Anthropic, Cloudflare — Nov 2025) —
  agents **write code** against tools presented as a filesystem of APIs, run it in a
  **sandbox**, and keep intermediate results *out of the model's context.* Reported
  reduction: **~150,000 → ~2,000 tokens (98.7%).**

These are not features to compete with. They are **proof the problem is real** — and a
signal about where the value is: **not "retrieve the right schema," but "run the work
somewhere the model doesn't have to watch."**

---

## The vision

**Fabrica is the vendor-neutral context layer that any Civitas agent gets for free.**

Where Anthropic's tools are Claude-only and Cloudflare's are Workers-locked, Fabrica
is **portable across models and self-hostable**, and it has one structural advantage
nobody else has:

> **Civitas is already a supervised-process runtime.** A supervised `AgentProcess` /
> `GenServer` is a natural, fault-tolerant, observable **sandbox**. The execution
> substrate that "code mode" needs is something Civitas already provides — and can
> harden all the way to microVMs for production.

So Fabrica delivers the whole context loop:

1. **Tool access as code, not schemas.** Tools become a code-API namespace with
   progressive disclosure. The agent writes code; Fabrica runs it in an isolated
   process; only the result returns to the window — **validated**, not just
   proposed: [a real spike](specs/archive/spikes/SPIKE-code-mode-execution.md)
   measured ~79% lower token cost *and* better correctness than the traditional
   tool-calling loop. A simple `find` retrieval mode remains as the fallback for
   models that don't do code mode — shared with skill discovery, see
   [`docs/retrieval.md`](docs/retrieval.md).
2. **A skills gateway** that conforms to the open **`SKILL.md`** standard — runtime-native
   skill loading with progressive disclosure.
3. **A memory interface** — a `MemoryStore` protocol with adapters for the mature
   players (Mem0, Zep, Letta, Cognee, LangMem). We *wrap*, we don't reinvent.
4. **A prompt library** — versioned, addressable prompts.

All four are **interface-first**: protocols + defaults in `fabrica`, adapters in
`fabrica-contrib` — the same pattern as `civitas`/`civitas-contrib` and
`presidium`/`presidium-contrib`.

---

## The differentiator: tiered isolation, up to microVMs — on any platform

Running model-generated code demands real isolation. Fabrica treats isolation as a
**pluggable, tiered `Sandbox` protocol** — start cheap in dev, harden for production
without changing agent code. **The backend is auto-detected per host OS, not a
user-facing config choice** — users don't care whether Firecracker, `srt`, or
libkrun is underneath, only that the problem is solved:

| Tier | Linux | macOS | Use when |
|---|---|---|---|
| 0 | subprocess, ~0ms | *(same)* | trusted code, local dev |
| 1 | **gVisor**, ~100ms | **`srt`** (Anthropic's Sandbox Runtime), measured p50 152ms | multi-tenant, compute-heavy |
| 2 | **Firecracker** — VMM-ready ~10.5ms, restore from snapshot **8.1–10.7ms measured on real hardware** | **libkrun** — works, but cold-boot-only (no snapshot/restore exists on this path, a permanent ceiling, not a bug) | **untrusted agent code, prod** |

(Windows: real but slower options exist — Hyper-V isolation for Tier 2, an
untested `srt` Windows mode for Tier 1. Deliberately deferred: small segment,
revisit only if a real gap surfaces. Full detail in
[`docs/isolation.md`](docs/isolation.md).)

Firecracker is the Linux production target: its own kernel per microVM, `jailer`
defence-in-depth, and snapshot/restore warm pools — real restore latency measured,
not cited (see [the Firecracker spike](specs/archive/spikes/SPIKE-firecracker-boot-restore-latency.md)).
Fabrica orchestrates the pool; the agent just calls a tool.

---

## How it fits Civitas and Presidium

- **Civitas** provides the process/supervision substrate Fabrica runs the sandbox in,
  plus the `ToolProvider`/`MCPClient` seams and OTEL tracing.
- **Presidium** governs it: grants decide which tools/skills a sandbox may touch;
  policy decides whether a code-mode run is allowed; the credential path (and tools
  like **tessera**) inject secrets the sandbox can *use but never see*.
- **Fabrica** is the neutral middle: it shapes and executes context, and emits the
  spans and audit events the other two consume.

Full seam map: [`docs/civitas-presidium-integration.md`](docs/civitas-presidium-integration.md).

---

## Plan

| Phase | Deliverable |
|---|---|
| **P0 — Thesis** (now) | This README + `docs/`. Supersede RFC 0001. Agree scope. |
| **P1 — Tools** | `find` fallback + tools-as-code namespace (**both validated by spike**); Tier 0/1 sandbox. |
| **P2 — Isolation** | `Sandbox` protocol; Firecracker (Tier 2) backend + warm pools. |
| **P3 — Skills** | `SKILL.md`-conformant skills gateway with progressive disclosure. |
| **P4 — Memory & Prompts** | `MemoryStore` protocol + adapters; versioned `PromptStore`. |

---

## Documentation

| Doc | What |
|---|---|
| [system-design.md](docs/system-design.md) | Internals: object model, deployment topology, error handling, state ownership — the layer below architecture.md, above contracts |
| [architecture.md](docs/architecture.md) | **Start here for a visual walkthrough** — diagrams of every layer, from platform context down to a single request's lifecycle |
| [personas.md](docs/personas.md) | Who Fabrica is for — human personas + JTBD, and the model as a non-human actor |
| [problem-definition.md](docs/problem-definition.md) | Per-persona problem statements, success metrics, non-goals — and the cross-cutting decisions that fell out of defining them |
| [context-layer.md](docs/context-layer.md) | The pillar framing and full scope |
| [tool-execution.md](docs/tool-execution.md) | Tools-as-code / code-mode design; `find` fallback |
| [retrieval.md](docs/retrieval.md) | The shared `Retriever` engine behind tools + skills discovery — one engine, rank-not-threshold, Rust-for-compute |
| [contracts/retriever.md](docs/contracts/retriever.md) | **Contracts** — implementation-ready signatures, error types, async behavior. |
| [contracts/sandbox.md](docs/contracts/sandbox.md) | Sandbox contract — corrects a real imprecision in system-design.md's warm-pool language, found by writing exact release() semantics. |
| [contracts/managers.md](docs/contracts/managers.md) | `PresidiumClient.check_grant`, the shared `execute_in_sandbox` helper, `ToolManager`, `SkillManager` |
| [contracts/memory.md](docs/contracts/memory.md) | `WorkingMemoryStore`, `Compactor`/`Summarizer` (DI'd), `MemoryStore`, `MemoryManager` facade |
| [prompts.md](docs/prompts.md) | The narrowest manager: storage/versioning/retrieval only — no rendering, no compression, both deliberately excluded |
| [contracts/prompts.md](docs/contracts/prompts.md) | `PromptStore`, `PromptManager` |
| [isolation.md](docs/isolation.md) | Tiered `Sandbox` protocol; gVisor → Firecracker → Kata |
| [skills-gateway.md](docs/skills-gateway.md) | `SKILL.md`-conformant skills, discovery via retrieval.md |
| [memory.md](docs/memory.md) | Three facets: working memory, compaction (harness-engineering primitive, DI'd Summarizer), long-term `MemoryStore` (wrap-don't-build) |
| [civitas-presidium-integration.md](docs/civitas-presidium-integration.md) | How Fabrica plugs into the platform |
| [landscape.md](docs/landscape.md) | Competitive research (Nov 2025), dated + sourced |
| [critique.md](docs/critique.md) | Design claims checked against six spikes — corrections, open decisions, and the one gap that matters most |

---

## License

Apache 2.0.
