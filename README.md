# Fabrica

**The context layer for Python agents.**

Part of the [Civitas](https://github.com/civitas-io/python-civitas) platform.

> **Status:** Pre-alpha — thesis & design. This repo currently hosts the vision and
> design docs. Code lands after the scope in [`docs/`](docs/) is agreed.

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
   process; only the result returns to the window. A simple `find_tools` retrieval
   mode remains as the fallback for models that don't do code mode.
2. **A skills gateway** that conforms to the open **`SKILL.md`** standard — runtime-native
   skill loading with progressive disclosure.
3. **A memory interface** — a `MemoryStore` protocol with adapters for the mature
   players (Mem0, Zep, Letta, Cognee, LangMem). We *wrap*, we don't reinvent.
4. **A prompt library** — versioned, addressable prompts.

All four are **interface-first**: protocols + defaults in `fabrica`, adapters in
`fabrica-contrib` — the same pattern as `civitas`/`civitas-contrib` and
`presidium`/`presidium-contrib`.

---

## The differentiator: tiered isolation, up to microVMs

Running model-generated code demands real isolation. Fabrica treats isolation as a
**pluggable, tiered `Sandbox` protocol** — start cheap in dev, harden for production
without changing agent code:

| Tier | Backend | Isolation | Cold start | Use when |
|---|---|---|---|---|
| 0 | In-process / subprocess | none / OS user | ~0 | trusted code, local dev |
| 1 | **gVisor** | user-space kernel | ~100 ms | multi-tenant, compute-heavy |
| 2 | **Firecracker microVM** | hardware (KVM) | ~125 ms boot, ~4 ms restore from snapshot | **untrusted agent code, prod** |
| 3 | **Kata Containers** | microVM in Kubernetes | ~60–150 ms | k8s-native multi-tenant |

Firecracker is the production target: its own kernel per microVM, `jailer`
defence-in-depth (cgroups + namespaces + seccomp + chroot), and snapshot/restore warm
pools (E2B reports sub-30 ms starts from snapshots). Fabrica orchestrates the pool;
the agent just calls a tool. See [`docs/isolation.md`](docs/isolation.md).

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
| **P1 — Tools** | `find_tools` fallback + tools-as-code namespace; Tier 0/1 sandbox. |
| **P2 — Isolation** | `Sandbox` protocol; Firecracker (Tier 2) backend + warm pools. |
| **P3 — Skills** | `SKILL.md`-conformant skills gateway with progressive disclosure. |
| **P4 — Memory & Prompts** | `MemoryStore` protocol + adapters; versioned `PromptStore`. |

---

## Documentation

| Doc | What |
|---|---|
| [context-layer.md](docs/context-layer.md) | The pillar framing and full scope |
| [tool-execution.md](docs/tool-execution.md) | Tools-as-code / code-mode design; `find_tools` fallback |
| [isolation.md](docs/isolation.md) | Tiered `Sandbox` protocol; gVisor → Firecracker → Kata |
| [skills-gateway.md](docs/skills-gateway.md) | `SKILL.md`-conformant skills |
| [memory.md](docs/memory.md) | `MemoryStore` protocol; wrap-don't-build |
| [civitas-presidium-integration.md](docs/civitas-presidium-integration.md) | How Fabrica plugs into the platform |
| [landscape.md](docs/landscape.md) | Competitive research (Nov 2025), dated + sourced |

---

## License

Apache 2.0.
