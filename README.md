# Fabrica

**The context layer for Python agents.**

Part of the [Civitas](https://github.com/civitas-io/python-civitas) platform.

> **Resuming after a break or a context compaction? Read [`HANDOFF.md`](HANDOFF.md)
> first** — it leads with the current state (what's done, what's genuinely
> open, what's next), then the full reasoning trail behind every decision.

> **Status:** Pre-alpha — design and validation complete for the whole object
> model plus both MCP directions (eight contracts, ten spikes
> [`specs/archive/spikes/`](specs/archive/spikes/) against real hardware and
> real API calls, not simulated). **Zero code exists yet, anywhere, by design**
> — see [`docs/critique.md`](docs/critique.md) for what held up under evidence
> and what got corrected. Code lands after a naming decision (a live PyPI
> collision, unresolved) and `plan-work` turn this into an implementation plan.

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

**This table describes the original phasing. It's kept for history, not
because it still matches reality** — every phase below is now designed and
contracted, not just planned, and MCP integration (both directions) exists as
a fifth capability area this table predates entirely. See `HANDOFF.md`'s
current-state section for what's actually true today.

| Phase | Deliverable | Actual status |
|---|---|---|
| **P0 — Thesis** | This README + `docs/`. Supersede RFC 0001. Agree scope. | Done |
| **P1 — Tools** | `find` fallback + tools-as-code namespace; Tier 0/1 sandbox. | Validated by spike, contracted (`Retriever`, `Sandbox`, `managers.md`) |
| **P2 — Isolation** | `Sandbox` protocol; Firecracker (Tier 2) backend + warm pools. | Contracted (`contracts/sandbox.md`); Tier 2 relay implementation unspiked |
| **P3 — Skills** | `SKILL.md`-conformant skills gateway with progressive disclosure. | Contracted (`SkillManager` in `managers.md`); exact frontmatter field list still open |
| **P4 — Memory & Prompts** | `MemoryStore` protocol + adapters; versioned `PromptStore`. | Contracted (`contracts/memory.md`, `contracts/prompts.md`); reframed mid-project into three memory facets, not just one |
| **P5 — MCP integration** *(not in the original plan)* | Fabrica as both an MCP client and server | Both directions designed and contracted |
| **P6 — Implementation** *(not started)* | Actual `fabrica/` Python package | Blocked on a naming decision (PyPI collision), then `plan-work` |

---

## Documentation

Grouped by the order this project was actually built in -- discovery through
contracts -- not insertion order. Start with `architecture.md` for the visual
tour, or `HANDOFF.md` for the current-state summary.

**Discovery & definition**

| Doc | What |
|---|---|
| [personas.md](docs/personas.md) | Who Fabrica is for -- human personas + JTBD, and the model as a non-human actor |
| [problem-definition.md](docs/problem-definition.md) | Per-persona problem statements, success metrics, non-goals |
| [context-layer.md](docs/context-layer.md) | The pillar framing and full scope |
| [landscape.md](docs/landscape.md) | Competitive research (Nov 2025), dated + sourced |

**Design**

| Doc | What |
|---|---|
| [architecture.md](docs/architecture.md) | **Start here for a visual walkthrough** -- diagrams of every layer, plus §1a's named library-first/low-coupling principle |
| [system-design.md](docs/system-design.md) | Internals: object model, deployment topology, error handling, state ownership |
| [tool-execution.md](docs/tool-execution.md) | Tools-as-code / code-mode design; `find` fallback |
| [retrieval.md](docs/retrieval.md) | The shared `Retriever` engine behind tools + skills discovery -- one engine, rank-not-threshold, Rust-for-compute |
| [isolation.md](docs/isolation.md) | Tiered `Sandbox` protocol; gVisor → Firecracker → Kata |
| [skills-gateway.md](docs/skills-gateway.md) | `SKILL.md`-conformant skills, discovery via retrieval.md |
| [memory.md](docs/memory.md) | Three facets: working memory, compaction (harness-engineering primitive, DI'd `Summarizer`), long-term `MemoryStore`. Includes a grounded "related work" section (Generative Agents, MemGPT, MemOS) explaining a deliberate divergence. |
| [prompts.md](docs/prompts.md) | The narrowest manager: storage/versioning/retrieval only. Plus a grounded "explored, not built" survey (provider-side caching, DSPy-style tuning, `PROMPT.md` portable format) |
| [mcp-integration.md](docs/mcp-integration.md) | `MCPToolNamespace` -- Fabrica as an MCP client, using `srt` (cross-platform) not Linux-only `bwrap` |
| [mcp-server.md](docs/mcp-server.md) | `FabricaMCPServer` -- Fabrica as an MCP server, preserving the two-path token-efficiency thesis externally, not just internally |
| [civitas-presidium-integration.md](docs/civitas-presidium-integration.md) | How Fabrica plugs into the platform |

**Validation**

| Doc | What |
|---|---|
| [critique.md](docs/critique.md) | Every design claim checked against evidence as it existed at the time -- corrections applied, not just proposed |
| `specs/archive/spikes/` | Ten spikes, real hardware/API evidence -- see `HANDOFF.md`'s arc section for the full list with findings |

**Contracts -- implementation-ready signatures, error types, async behavior**

| Doc | What |
|---|---|
| [contracts/retriever.md](docs/contracts/retriever.md) | `Retriever`/`RetrieverBackend` |
| [contracts/sandbox.md](docs/contracts/sandbox.md) | `Sandbox`/`SandboxPool` -- corrected a real warm-pool reuse bug found while writing exact `release()` semantics |
| [contracts/managers.md](docs/contracts/managers.md) | `PresidiumClient.check_grant`, `execute_in_sandbox`, `ToolManager`, `SkillManager` |
| [contracts/memory.md](docs/contracts/memory.md) | `WorkingMemoryStore`, `Compactor`/`Summarizer` (DI'd), `MemoryStore`, `MemoryManager`, `NullCompactor` |
| [contracts/prompts.md](docs/contracts/prompts.md) | `PromptStore`, `PromptManager` -- `cacheable`/`cache_boundary`, `PROMPT.md` `load()`, `list_names()` |
| [contracts/civitas-bridge.md](docs/contracts/civitas-bridge.md) | `CivitasBridge` -- reconciled against `python-civitas`'s real `Runtime.spawn`/`StateStore` source |
| [contracts/mcp-integration.md](docs/contracts/mcp-integration.md) | `MCPClient`, `MCPToolNamespace` |
| [contracts/mcp-server.md](docs/contracts/mcp-server.md) | `FabricaMCPServer` |

---

## License

Apache 2.0.
