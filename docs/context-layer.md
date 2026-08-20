# Fabrica as the Context Layer

**Status:** Design · **Last updated:** 2026-08

---

## The pillar

The Civitas platform has three layers. Two exist; this doc defines the third.

| Layer | Product | One-line job | Question it answers |
|---|---|---|---|
| Runtime | Civitas | keep agents alive | *Is the agent still running?* |
| Control | Presidium | keep agents accountable | *Is the agent allowed to do this?* |
| **Context** | **Fabrica** | **shape what the agent sees & how it acts** | *What is in the window, and where does the work run?* |

Fabrica is the missing pillar. Today its concerns are scattered — tool retrieval
(RFC 0001), prompt libraries, skills, memory — across ideas and repos. This doc
consolidates them into one product family with one thesis.

## Scope: what Fabrica owns

Everything that governs **the content and execution of the context window**:

1. **Tool access** — how tools reach the model and where tool code runs.
   Headline: tools-as-code + sandboxed execution (**validated** — see the
   [code-mode spike](../specs/archive/spikes/SPIKE-code-mode-execution.md)).
   Fallback: `find` retrieval, shared with skills — see [retrieval.md](retrieval.md).
2. **Skills** — packaged, reusable agent capabilities, loaded on demand, conformant
   to the open `SKILL.md` standard. Discovery shares the same `Retriever` engine as
   tools — see [retrieval.md](retrieval.md).
3. **Memory** — session and long-term recall, via a protocol with adapters for
   mature backends (not a reimplementation).
4. **Prompts** — versioned, addressable prompt management.
5. **Isolation** — the sandbox substrate that tool/skill code executes in, tiered from
   subprocess to microVM.
6. **MCP surfaces, both directions** *(added after this list was first written --
   real, built code now, not a late addition to the thesis)*: Fabrica as an
   MCP **client**, consuming external MCP servers as a `ToolNamespace` source
   (`MCPClient`/`MCPToolNamespace` — see [mcp-integration.md](mcp-integration.md));
   and Fabrica as an MCP **server**, exposing its own tools/skills/memory/prompts
   as one MCP endpoint (`FabricaMCPServer` — see [mcp-server.md](mcp-server.md)).
   Both directions were explicitly re-checked against the "not Fabrica's
   concern" boundary below before being built — see the note there for why
   neither is the rejected "generic MCP proxy" category.

## Scope: what Fabrica does NOT own

Keep the pillar boundaries clean, exactly as Civitas/Presidium do:

| Not Fabrica's concern | Who owns it | Why |
|---|---|---|
| Process lifecycle, supervision, transport | Civitas | runtime primitive |
| OTEL span plumbing | Civitas | Fabrica *emits*, Civitas *collects* |
| Policy: ALLOW/DENY of a tool or code run | Presidium | governance decision |
| Grants: which tools/skills an agent may touch | Presidium | authorization |
| Credential vault / token exchange | Presidium (+ tessera at CLI) | governance -- see [credentials.md](credentials.md) for why `Sandbox` itself gets no credential-injection mechanism at all |
| A generic MCP proxy/registry | nobody — it's commoditized infra | see landscape.md |
| Governed LLM/MCP gateway | Presidium (wraps agentgateway) | governance |

**The line that matters, since scope item 6 above looks superficially similar
to the rejected "generic MCP proxy" row**: a generic proxy aggregates *many*
third-party MCP servers for *many* consumers as a standalone product — that's
the commoditized, not-Fabrica category. `MCPClient` (Fabrica *consuming* one
or more MCP servers as a tool source, still governed by the same grants/
sandbox as any other tool) and `FabricaMCPServer` (Fabrica exposing its *own*
capabilities, the same pattern as GitHub's or Claude Desktop's own MCP
servers) are both the opposite shape. Checked explicitly, not assumed — see
[mcp-server.md](mcp-server.md#the-constraint-checked-again-in-the-new-direction).

Fabrica shapes and runs context. It does not run the process (Civitas) or decide
permission (Presidium). It **emits** the spans and audit events those layers consume
— **real now, all ten spans named in
[system-design.md §7](system-design.md#7-observability-spans-this-system-emits)**
(originally nine -- `PromptManager` gained its own `fabrica.prompt.get`/
`fabrica.prompt.put` pair afterward, closing a gap where it emitted
nothing at all, not just a missing attribute),
closing what `self-reflection-report.md` §3.3 found was the largest gap
between this doc's claim and the real code. Credential injection and real
usage/budget metering remain unbuilt — see [`docs/PLAN.md`](PLAN.md).

## Interface-first, mirroring the platform

```
fabrica/            # protocols + lightweight defaults, depends only on civitas
  ToolNamespace, Sandbox, SkillStore, MemoryStore, PromptStore, Retriever  (protocols)
  find fallback (KeywordBackend: pure-Python `rank-bm25`, NOT Rust+PyO3 as
  originally sketched here -- a deliberate v1 reversal of the engineering
  principle below, see its own note for why), subprocess sandbox,
  filesystem skill loader  (defaults)

fabrica-contrib/    # adapters, opt-in extras
  [firecracker] [srt] [libkrun] [gvisor] [kata]  # sandbox backends, platform-dispatched
  [prx] [llamaindex] [langchain]       # Retriever embedding backends
  [mem0] [zep] [letta] [cognee]        # memory backends
  [mcp]                                # MCP tool source
```

`pip install fabrica` gives you working defaults with zero infrastructure.
`fabrica-contrib[firecracker]` (etc.) upgrades a single component for production —
same interface, no agent-code changes. This is the exact contract Civitas and
Presidium already ship.

**Decided, not just found stale**: this split was never actually built.
Today everything — including `mcp`/`uvicorn` (the MCP client/server's real
dependencies) and `FirecrackerSandbox` — ships as required code and
dependencies in one package, `fabrica-context`. Discussed directly rather
than resolved unilaterally: **deliberately deferred until closer to a real
release**, not abandoned and not done by accident. Reasoning: there is no
external user yet for the zero-infra-install property to matter to in
practice, and building the split now would mean guessing its final shape
before Tier 1 isolation, managed-sandbox adapters, and real memory-backend
adapters (`fabrica-contrib[mem0]` etc.) exist to actually validate it
against — each of those would otherwise need to retroactively fit into a
split decided too early. Revisit before or at the point a real release is
planned, or the moment a real user's install footprint becomes a genuine
complaint, whichever comes first. Tracked as a decided (not open) item in
[`docs/PLAN.md`](PLAN.md).

## Deployment modes

Like the rest of the platform, every component runs in two modes:

- **Library mode** — in-process inside a Civitas deployment. Zero infra. Dev + small.
- **Service mode** — a supervised `GenServer` on the Civitas bus (e.g. a shared
  sandbox pool or memory service). Same interface, shared state, central management.

## Engineering principle: Rust for compute, Python for interface

Where Fabrica **builds** (not wraps) a compute-intensive internal component, prefer
implementing it in **Rust with a Python binding** (e.g. via PyO3/maturin), not pure
Python. This mirrors the platform's own toolchain precedent — prx's embedded
retrieval model is exactly this shape (Rust core, zero-copy, no runtime dependency)
— rather than inventing a new pattern.

This does **not** contradict the "zero infra for hello-world" success metric
(Priya's, in `problem-definition.md`): a compiled Rust extension ships as a normal
wheel. `pip install fabrica` stays exactly as simple — there is no separate Rust
toolchain a user ever sees or installs. The principle is about where the *compute*
lives, not what the *user* has to do.

**Correction found during implementation, not resolved here originally**:
the default keyword-retrieval backend named below as the first likely
candidate shipped v1 as pure-Python `rank-bm25` instead, deliberately --
no real performance evidence existed to justify the Rust/PyO3 tooling cost
(a new build toolchain, a compiled-extension release pipeline) before a
single real user had exercised the pure-Python version at all. "Ship the
default, revisit if forced" -- the same pattern applied elsewhere in this
project (sandbox language, `eager`'s per-deployment override). The
principle itself is not abandoned; this is a stated, reasoned exception
for the *first* real instance of it, not a quiet reversal of the rule.
See [retrieval.md](retrieval.md#backends--rust-for-the-built-parts-wrap-everything-else)
for the current, accurate state, and `HANDOFF.md`/`docs/PLAN.md` for the
reasoning trail.

Other likely candidates as implementation proceeds (not resolved here,
still flagged for later): any local embedding computation Fabrica ever
implements itself (as opposed to wrapping), and sandbox-pool
bookkeeping/scheduling if it turns out to be compute-bound rather than
I/O-bound. Wrapped third-party
libraries (Mem0, Zep, LlamaIndex, prx itself) are unaffected — this principle only
applies to what Fabrica builds, consistent with "wrap, don't build" everywhere else
in this doc set.

## Why this framing wins

- **Symmetry sells.** "Runtime, Control, Context — Civitas, Presidium, Fabrica" is a
  story an enterprise architect can hold in their head.
- **It's defensible.** The commoditized parts (generic gateways) are explicitly *not*
  Fabrica. The defensible parts (vendor-neutral sandboxed execution on a supervised
  runtime; runtime-native skills) *are*.
- **It absorbs the scattered ideas** (RFC 0001 tool retrieval, prompt library, skills
  gateway, promptshrink compression) into one coherent product instead of four
  half-products.
