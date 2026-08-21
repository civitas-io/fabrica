# Fabrica

**The context layer for Python agents.**

Part of the [Civitas](https://github.com/civitas-io/python-civitas) platform.
Distributed as `fabrica-context` (`pip install fabrica-context`) — `fabrica`
itself is already taken on PyPI by an unrelated project; the project and repo
keep the "Fabrica" name, only the package name differs.

> **Resuming after a break or a context compaction? Read [`HANDOFF.md`](https://github.com/civitas-io/fabrica/blob/main/HANDOFF.md)
> first** — it leads with the current state (what's done, what's genuinely
> open, what's next), then the full reasoning trail behind every decision.

> **Status:** Pre-alpha, but substantially real. Full discovery-through-contracts
> design arc (nine contracts, fourteen spikes in
> [`specs/archive/spikes/`](https://github.com/civitas-io/fabrica/tree/main/specs/archive/spikes/) against real hardware and real
> API calls, not simulated) has produced actual working code, not just designs.
> **All six object-model contracts are built and tested** — `Retriever`,
> `Sandbox`/`SandboxPool`, `ToolManager`, `SkillManager`, `MemoryManager` (all
> three facets), `PromptManager`, plus `CivitasBridge` itself — 195 local tests,
> clean `ruff`/`mypy --strict`. **Both MCP directions are real**: `MCPClient`
> (Fabrica consuming external MCP servers) and `FabricaMCPServer` (Fabrica
> exposing its own tools/skills/memory/prompts as one MCP endpoint).
> **Self-hosted Tier 2 isolation is real, not just designed**: `FirecrackerSandbox`
> is validated end to end on real hardware — including a real tool call crossing
> a real microVM boundary over `vsock` — and is reachable through real,
> automatic platform dispatch (14 additional tests that only run on real
> Linux+KVM+Firecracker, skipped elsewhere). A reusable deployment script exists
> ([`scripts/build_firecracker_rootfs.sh`](https://github.com/civitas-io/fabrica/blob/main/scripts/build_firecracker_rootfs.sh))
> so a second person can actually produce a working image, not just read a
> spike transcript.
>
> **Honest gaps, not hidden**: a self-reflection pass
> ([`docs/self-reflection-report.md`](https://github.com/civitas-io/fabrica/blob/main/docs/self-reflection-report.md)) found
> real drift between the original design and what shipped — the planned
> `fabrica`/`fabrica-contrib` package split was never built (this package
> currently ships everything, including `mcp`/`uvicorn`, as required
> dependencies; **now a decided, deliberate deferral until closer to a real
> release**, not an open question). **Real OTEL span emission is now
> built** — all ten spans named in `system-design.md §7` are real (originally
> nine -- `PromptManager` gained `fabrica.prompt.get`/`fabrica.prompt.put`
> afterward), not a log stand-in, closing what was the largest gap found.
> Credential injection into `Sandbox` and real usage/budget metering are
> both built too, all tracked, in order, in [`docs/PLAN.md`](https://github.com/civitas-io/fabrica/blob/main/docs/PLAN.md),
> the active work queue — not silently deferred.

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
   proposed: [a real spike](https://github.com/civitas-io/fabrica/blob/main/specs/archive/spikes/SPIKE-code-mode-execution.md)
   measured ~79% lower token cost *and* better correctness than the traditional
   tool-calling loop. A simple `find` retrieval mode remains as the fallback for
   models that don't do code mode — shared with skill discovery, see
   [`docs/retrieval.md`](https://github.com/civitas-io/fabrica/blob/main/docs/retrieval.md).
2. **A skills gateway** that conforms to the open **`SKILL.md`** standard — runtime-native
   skill loading with progressive disclosure.
3. **A memory interface** — a `MemoryStore` protocol with adapters for the mature
   players (Mem0, Zep, Letta, Cognee, LangMem). We *wrap*, we don't reinvent.
4. **A prompt library** — versioned, addressable prompts.

All four are designed **interface-first**: protocols + defaults in `fabrica`,
adapters meant to live in an opt-in `fabrica-contrib` — the same pattern as
`civitas`/`civitas-contrib` and `presidium`/`presidium-contrib`. **Honest
current state**: that split hasn't been built — today everything, including
the MCP client/server (which pull in `mcp` and `uvicorn` as required
dependencies) and `FirecrackerSandbox`, ships in one package,
`fabrica-context`. **Decided, not left unresolved**: deliberately deferred
until closer to a real release (no external user yet for the zero-infra-
install property to matter to in practice, and building the split before
Tier 1/managed-sandbox/real-memory-backend adapters exist means guessing its
shape too early) — see [`docs/context-layer.md`](https://github.com/civitas-io/fabrica/blob/main/docs/context-layer.md#interface-first-mirroring-the-platform)
for the full reasoning.

---

## The differentiator: tiered isolation, up to microVMs — on any platform

Running model-generated code demands real isolation. Fabrica treats isolation as a
**pluggable, tiered `Sandbox` protocol** — start cheap in dev, harden for production
without changing agent code. **The backend is auto-detected per host OS, not a
user-facing config choice** — users don't care whether Firecracker, `srt`, or
libkrun is underneath, only that the problem is solved:

| Tier | Linux | macOS | Use when | Status |
|---|---|---|---|---|
| 0 | subprocess, ~0ms | *(same)* | trusted code, local dev | **Implemented** (`SubprocessSandbox`) |
| 1 | **gVisor**, ~100ms | **`srt`** (Anthropic's Sandbox Runtime), measured p50 152ms | multi-tenant, compute-heavy | Not yet implemented |
| 2 | **Firecracker** — VMM-ready ~10.5ms, restore from snapshot **8.1–10.7ms measured on real hardware** | **libkrun** — works, but cold-boot-only (no snapshot/restore exists on this path, a permanent ceiling, not a bug) | **untrusted agent code, prod** | **Implemented on Linux** (`FirecrackerSandbox`) — v1 is cold-boot-only (snapshot/restore not yet combined with the real `vsock` bridge); macOS `libkrun` not yet implemented |

(Windows: real but slower options exist — Hyper-V isolation for Tier 2, an
untested `srt` Windows mode for Tier 1. Deliberately deferred: small segment,
revisit only if a real gap surfaces. Full detail in
[`docs/isolation.md`](https://github.com/civitas-io/fabrica/blob/main/docs/isolation.md).)

**Real platform dispatch exists today**: `select_sandbox_backend()` auto-detects
Linux + real KVM + real Firecracker artifacts and returns `FirecrackerSandbox`;
otherwise returns `SubprocessSandbox`. `CivitasBridge.build()` calls it
automatically — not a hardcoded choice, and not something agent code ever sees.
Firecracker's own kernel-per-microVM isolation and snapshot/restore warm pools
are the Linux production target; `jailer` defense-in-depth hardening is a named,
real follow-on ([`docs/PLAN.md`](https://github.com/civitas-io/fabrica/blob/main/docs/PLAN.md)), not yet built. Restore latency
is measured on real hardware, not cited (see
[the Firecracker spike](https://github.com/civitas-io/fabrica/blob/main/specs/archive/spikes/SPIKE-firecracker-boot-restore-latency.md));
the real `vsock` tool-call bridge crossing an actual microVM boundary is
validated in
[a second spike](https://github.com/civitas-io/fabrica/blob/main/specs/archive/spikes/SPIKE-firecracker-vsock-callback-bridge.md).
A reusable script builds the deployable rootfs image — see
[`docs/deployment/firecracker-rootfs.md`](https://github.com/civitas-io/fabrica/blob/main/docs/deployment/firecracker-rootfs.md).

---

## How it fits Civitas and Presidium

- **Civitas** provides the process/supervision substrate Fabrica runs the sandbox in,
  plus the `ToolProvider`/`MCPClient` seams and OTEL tracing.
- **Presidium** governs it: grants decide which tools/skills a sandbox may touch;
  policy decides whether a code-mode run is allowed; the credential path (and tools
  like **tessera**) inject secrets the sandbox can *use but never see*.
- **Fabrica** is the neutral middle: it shapes and executes context, and
  emits the spans and audit events the other two consume — **real now, all
  ten spans**: `fabrica.observability.Tracer`/`Span` are structural
  Protocols matching `civitas.observability.tracer.Tracer`'s real, public
  shape exactly (a real finding: Civitas doesn't use OTEL's global
  provider registry, so this had to match its actual mechanism, not just
  call the generic OTEL API into a void). Every manager defaults to a
  real no-op (`NullTracer()`); a real deployment passes a real
  `civitas.observability.tracer.Tracer()` into `CivitasBridge` to get
  real, nested spans end to end. See
  [`docs/system-design.md §7`](https://github.com/civitas-io/fabrica/blob/main/docs/system-design.md#7-observability-spans-this-system-emits).

Full seam map: [`docs/civitas-presidium-integration.md`](https://github.com/civitas-io/fabrica/blob/main/docs/civitas-presidium-integration.md).

---

## Plan

**This table describes the original phasing. It's kept for history, not
because it still matches reality** — every phase below is now built, not just
designed, and MCP integration (both directions) and self-hosted Tier 2
isolation exist as capability areas this table predates entirely.

| Phase | Deliverable | Actual status |
|---|---|---|
| **P0 — Thesis** | This README + `docs/`. Supersede RFC 0001. Agree scope. | Done |
| **P1 — Tools** | `find` fallback + tools-as-code namespace; Tier 0/1 sandbox. | **Built**: `Retriever`, `SubprocessSandbox` (Tier 0), `ToolManager`. Tier 1 (`gVisor`/`srt`) not yet built. |
| **P2 — Isolation** | `Sandbox` protocol; Firecracker (Tier 2) backend + warm pools. | **Built**: `FirecrackerSandbox`, real platform dispatch, `SandboxPool` warm pool + `close()`. Cold-boot-only v1 (no snapshot/restore yet); `jailer` hardening not yet built. |
| **P3 — Skills** | `SKILL.md`-conformant skills gateway with progressive disclosure. | **Built**: `SkillManager`, validated against the real 81-skill `bigpowers` catalog. |
| **P4 — Memory & Prompts** | `MemoryStore` protocol + adapters; versioned `PromptStore`. | **Built**: `MemoryManager` (three facets: working memory, `RecencyCompactor`/`NullCompactor`, long-term store), `PromptManager`. Only the local in-memory + `CivitasBridge`-persisted stores exist; Mem0/Zep/Letta/Cognee adapters remain future `fabrica-contrib` work. |
| **P5 — MCP integration** *(not in the original plan)* | Fabrica as both an MCP client and server | **Built**: `MCPClient`/`MCPToolNamespace` (client direction), `FabricaMCPServer` (server direction, stdio + HTTP transports). |
| **P6 — Implementation** | Actual `fabrica-context` Python package | **All six object-model contracts built and tested end to end**, plus `CivitasBridge` (service-mode persistence via `PersistedMemoryStore`/`PersistedPromptStore`). 195 local tests + 14 real-hardware-only Firecracker tests. |
| **P7 — Self-reflection** *(not in the original plan)* | Audit the real code/docs against the founding vision | **Done**: [`docs/self-reflection-report.md`](https://github.com/civitas-io/fabrica/blob/main/docs/self-reflection-report.md) found real drift (package split never built, stale Rust/PyO3 doc claim, observability ~unbuilt); tracked as an ordered queue in [`docs/PLAN.md`](https://github.com/civitas-io/fabrica/blob/main/docs/PLAN.md), now the active work in progress. |

---

## Documentation

Grouped by the order this project was actually built in -- discovery through
contracts -- not insertion order. Start with `architecture.md` for the visual
tour, or `HANDOFF.md` for the current-state summary.

**Discovery & definition**

| Doc | What |
|---|---|
| [personas.md](https://github.com/civitas-io/fabrica/blob/main/docs/personas.md) | Who Fabrica is for -- human personas + JTBD, and the model as a non-human actor |
| [problem-definition.md](https://github.com/civitas-io/fabrica/blob/main/docs/problem-definition.md) | Per-persona problem statements, success metrics, non-goals |
| [context-layer.md](https://github.com/civitas-io/fabrica/blob/main/docs/context-layer.md) | The pillar framing and full scope |
| [landscape.md](https://github.com/civitas-io/fabrica/blob/main/docs/landscape.md) | Competitive research (Nov 2025), dated + sourced |

**Design**

| Doc | What |
|---|---|
| [architecture.md](https://github.com/civitas-io/fabrica/blob/main/docs/architecture.md) | **Start here for a visual walkthrough** -- diagrams of every layer, plus §1a's named library-first/low-coupling principle |
| [system-design.md](https://github.com/civitas-io/fabrica/blob/main/docs/system-design.md) | Internals: object model, deployment topology, error handling, state ownership |
| [tool-execution.md](https://github.com/civitas-io/fabrica/blob/main/docs/tool-execution.md) | Tools-as-code / code-mode design; `find` fallback |
| [retrieval.md](https://github.com/civitas-io/fabrica/blob/main/docs/retrieval.md) | The shared `Retriever` engine behind tools + skills discovery -- one engine, rank-not-threshold, Rust-for-compute |
| [isolation.md](https://github.com/civitas-io/fabrica/blob/main/docs/isolation.md) | Tiered `Sandbox` protocol; gVisor → Firecracker → Kata |
| [skills-gateway.md](https://github.com/civitas-io/fabrica/blob/main/docs/skills-gateway.md) | `SKILL.md`-conformant skills, discovery via retrieval.md |
| [memory.md](https://github.com/civitas-io/fabrica/blob/main/docs/memory.md) | Three facets: working memory, compaction (harness-engineering primitive, DI'd `Summarizer`), long-term `MemoryStore`. Includes a grounded "related work" section (Generative Agents, MemGPT, MemOS) explaining a deliberate divergence. |
| [prompts.md](https://github.com/civitas-io/fabrica/blob/main/docs/prompts.md) | The narrowest manager: storage/versioning/retrieval only. Plus a grounded "explored, not built" survey (provider-side caching, DSPy-style tuning, `PROMPT.md` portable format) |
| [mcp-integration.md](https://github.com/civitas-io/fabrica/blob/main/docs/mcp-integration.md) | `MCPToolNamespace` -- Fabrica as an MCP client, using `srt` (cross-platform) not Linux-only `bwrap` |
| [mcp-server.md](https://github.com/civitas-io/fabrica/blob/main/docs/mcp-server.md) | `FabricaMCPServer` -- Fabrica as an MCP server, preserving the two-path token-efficiency thesis externally, not just internally |
| [civitas-presidium-integration.md](https://github.com/civitas-io/fabrica/blob/main/docs/civitas-presidium-integration.md) | How Fabrica plugs into the platform |
| [credentials.md](https://github.com/civitas-io/fabrica/blob/main/docs/credentials.md) | Why `Sandbox` gets no credential-injection mechanism at all -- validated against a real, separately-built credential broker (Tessera) |

**Validation**

| Doc | What |
|---|---|
| [critique.md](https://github.com/civitas-io/fabrica/blob/main/docs/critique.md) | Every design claim checked against evidence as it existed at the time -- corrections applied, not just proposed |
| [self-reflection-report.md](https://github.com/civitas-io/fabrica/blob/main/docs/self-reflection-report.md) | A later, separate audit -- real code/docs checked against the founding vision after implementation, not just design claims checked against spike evidence |
| `specs/archive/spikes/` | Fifteen spikes, real hardware/API evidence -- see `HANDOFF.md`'s arc section for the full list with findings |

**Contracts -- implementation-ready signatures, error types, async behavior**

| Doc | What |
|---|---|
| [contracts/retriever.md](https://github.com/civitas-io/fabrica/blob/main/docs/contracts/retriever.md) | `Retriever`/`RetrieverBackend` |
| [contracts/sandbox.md](https://github.com/civitas-io/fabrica/blob/main/docs/contracts/sandbox.md) | `Sandbox`/`SandboxPool` -- corrected a real warm-pool reuse bug found while writing exact `release()` semantics |
| [contracts/managers.md](https://github.com/civitas-io/fabrica/blob/main/docs/contracts/managers.md) | `PresidiumClient.check_grant`, `execute_in_sandbox`, `ToolManager`, `SkillManager` |
| [contracts/memory.md](https://github.com/civitas-io/fabrica/blob/main/docs/contracts/memory.md) | `WorkingMemoryStore`, `Compactor`/`Summarizer` (DI'd), `MemoryStore`, `MemoryManager`, `NullCompactor` |
| [contracts/prompts.md](https://github.com/civitas-io/fabrica/blob/main/docs/contracts/prompts.md) | `PromptStore`, `PromptManager` -- `cacheable`/`cache_boundary`, `PROMPT.md` `load()`, `list_names()` |
| [contracts/civitas-bridge.md](https://github.com/civitas-io/fabrica/blob/main/docs/contracts/civitas-bridge.md) | `CivitasBridge` -- reconciled against `python-civitas`'s real `Runtime.spawn`/`StateStore` source |
| [contracts/mcp-integration.md](https://github.com/civitas-io/fabrica/blob/main/docs/contracts/mcp-integration.md) | `MCPClient`, `MCPToolNamespace` |
| [contracts/mcp-server.md](https://github.com/civitas-io/fabrica/blob/main/docs/contracts/mcp-server.md) | `FabricaMCPServer` |
| [contracts/managed-sandbox.md](https://github.com/civitas-io/fabrica/blob/main/docs/contracts/managed-sandbox.md) | `ManagedSandboxAdapter`, `CallbackBridge`, `TunnelProvider` -- interfaces only, no provider implemented yet |

**Deployment**

| Doc | What |
|---|---|
| [deployment/firecracker-rootfs.md](https://github.com/civitas-io/fabrica/blob/main/docs/deployment/firecracker-rootfs.md) | How to build a real, deployable `FirecrackerSandbox` rootfs image -- exact `sudo` scoping included |

**Active work**

| Doc | What |
|---|---|
| [PLAN.md](https://github.com/civitas-io/fabrica/blob/main/docs/PLAN.md) | The single ordered work queue -- self-reflection fixes first, then the remaining backlog, easiest to most complex |

---

## License

Apache 2.0.
