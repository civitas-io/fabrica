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

```mermaid
graph TB
    subgraph CTX["CONTEXT LAYER — Fabrica"]
        direction LR
        F1["Tools<br/>(code-mode + find)"]
        F2["Skills<br/>(SKILL.md + find)"]
        F3["Memory<br/>(wraps Mem0/Zep/...)"]
        F4["Prompts<br/>(PromptStore)"]
        F5["Sandbox<br/>(tiered isolation)"]
    end
    subgraph CTL["CONTROL LAYER — Presidium"]
        direction LR
        P1["Policy / Grants"]
        P2["HITL / Approval"]
        P3["Usage Ledger / Budgets"]
    end
    subgraph RT["RUNTIME LAYER — Civitas"]
        direction LR
        C1["Supervision Trees"]
        C2["Message Bus"]
        C3["OTEL Tracing"]
    end

    CTX -- "grants/policy checks<br/>before every action" --> CTL
    CTL -- "ALLOW / DENY / REQUIRE_APPROVAL" --> CTX
    CTX -- "runs as supervised GenServers" --> RT
    RT -- "restart on crash, transport, spans" --> CTX
```

**The one-line version:** Civitas keeps agents alive. Presidium keeps them
accountable. Fabrica decides what they see and how they act on it. Full detail:
[context-layer.md](context-layer.md).

---

## 2. The two ways a model gets a capability

Fabrica offers exactly two paths, deliberately — a headline and a fallback, not a
menu of options. **Both are now validated by spike, not just designed.**

```mermaid
graph TD
    Model((Model)) --> Decision{Can the host<br/>write &amp; execute code?}
    Decision -- yes --> Headline["<b>Headline: code-mode</b><br/>writes code against ToolNamespace<br/>executes in Sandbox<br/>only the result returns"]
    Decision -- no --> Fallback["<b>Fallback: find()</b><br/>one meta-tool, server-side match<br/>returns matched schemas directly"]

    Headline -.->|"validated: ~79% cheaper AND<br/>more correct than direct calls<br/>(3/3 runs)"| Evidence1[SPIKE-code-mode-execution.md]
    Fallback -.->|"validated: near-flat token<br/>cost regardless of registry size"| Evidence2[SPIKE-tool-retrieval-token-overhead.md]
```

**Why code-mode wins, concretely** (not just "it's cheaper"):

| | Traditional tool-calling | Code-mode |
|---|---|---|
| Filtering/counting | model estimates by reading text | real Python arithmetic in the sandbox |
| Measured correctness | **wrong in 3/3 test runs** (16–48% off) | **exact in 3/3 test runs** |
| Token cost (same task) | 23,392–23,592 | 4,826–4,904 (~79% less) |

Full design: [tool-execution.md](tool-execution.md). Evidence:
[SPIKE-code-mode-execution.md](../specs/archive/spikes/SPIKE-code-mode-execution.md).

---

## 3. Code-mode execution, step by step

```mermaid
sequenceDiagram
    participant M as Model
    participant F as Fabrica
    participant S as Sandbox (tiered)
    participant T as Real tools/data

    M->>F: sees ToolNamespace.stubs() (progressive disclosure)
    M->>F: writes code calling stubs it opened
    F->>S: Sandbox.run(code)
    activate S
    S->>T: executes: list, filter, read, aggregate...
    T-->>S: raw results — large, stays inside the sandbox
    deactivate S
    S-->>F: only the final small result
    F-->>M: result enters model context
    Note over M,T: Loops, joins, retries, intermediate data —<br/>none of it ever touches the model's context window.
```

This is the mechanism behind the ~98.7% token reduction Anthropic/Cloudflare
reported (`landscape.md §1`) — Fabrica's version is vendor-neutral and
self-hostable, running on a Civitas-supervised process instead of a
vendor-locked platform.

---

## 4. The shared retrieval engine (tools + skills, unified)

Both the `find()` fallback and skill discovery run on **one** engine, not two —
resolved this way after measurement showed the original separate skill-index
design wasn't actually flat ([SPIKE-skill-progressive-disclosure.md](../specs/archive/spikes/SPIKE-skill-progressive-disclosure.md)).

```mermaid
graph LR
    Tool["Tool<br/>(Indexable, kind=tool)"] --> R[["Retriever engine"]]
    Skill["Skill<br/>(Indexable, kind=skill)"] --> R
    Model((Model)) -- "find(query, kind, limit)" --> R
    R -- "RankedMatch list<br/>(rank only — never<br/>absolute score)" --> Model

    R --> KW["KeywordBackend<br/><b>Rust + PyO3</b> — default"]
    R --> PRX["PrxBackend<br/>fabrica-contrib — validated<br/>100% precision@3"]
    R --> LI["LlamaIndex / LangChain<br/>fabrica-contrib — wrapped"]

    Memory["Memory<br/>(different semantics — scoped,<br/>not a shared registry)"] -.->|"shares the same<br/>engine underneath"| R
```

**Two hard rules baked into the protocol**, both surfaced by spike, not
guessed at:
1. **Rank, never absolute threshold** — correct hits and near-misses can land in
   the same low score band (0.01–0.04 observed).
2. **Persistent-process integration** for any external backend (like prx) — a
   supervised Civitas child, not a fresh subprocess per call.

Full design: [retrieval.md](retrieval.md).

---

## 5. Isolation: platform-dispatched, not one technology

The backend is **auto-detected by host OS and hidden from users** — a deliberate
exception to how Civitas normally exposes config (transport selection *is* a
user choice; isolation backend is not).

```mermaid
graph TD
    Start(["Sandbox factory<br/>auto-detects host OS"]) --> Linux
    Start --> macOS
    Start --> Windows

    subgraph Linux["Linux"]
        L0["Tier 0: subprocess — ~0ms"]
        L1["Tier 1: gVisor — ~100ms"]
        L2["Tier 2: Firecracker<br/><b>restore: 8–11ms, measured</b><br/>real bare-metal hardware"]
        L3["Tier 3: Kata — k8s multi-tenant"]
    end

    subgraph macOS["macOS"]
        M0["Tier 0: subprocess — ~0ms"]
        M1["Tier 1: srt — <b>p50 152ms, measured</b><br/>real write/network denial confirmed"]
        M2["Tier 2: libkrun — works, but<br/><b>cold-boot only, permanently</b><br/>(no snapshot/restore exists)"]
    end

    subgraph Windows["Windows — deliberately deferred"]
        W0["Tier 0: subprocess"]
        W1["Tier 1: srt Windows mode — untested"]
        W2["Tier 2: Hyper-V — real, but slow (s–min)"]
    end
```

**Two decisions worth remembering, both made explicitly, not by default:**
- macOS Tier 2 ships despite the cold-boot ceiling — *"snapshot/restore is great
  to have, not a must."*
- Windows is deliberately under-invested — small segment, revisit only if a real
  gap forces it.

Full design + the corrected boot-time distinction (VMM-ready vs. actually-usable):
[isolation.md](isolation.md).

---

## 6. Memory: wrap, with a working default — not the raw library

```mermaid
graph LR
    App["Agent code"] -- "write / search / get / forget<br/>(Scope: user, session, agent, team)" --> MS[["MemoryStore protocol"]]
    MS --> Default["Default: in-process<br/>SQLite / vector — zero infra"]
    MS --> Mem0["fabrica-contrib[mem0]<br/><b>validated by spike</b> —<br/>pinned local config shipped,<br/>not Mem0's raw defaults"]
    MS --> Others["Zep / Letta / Cognee / LangMem<br/>— designed, not yet spiked"]
```

The spike found Mem0's own defaults require an OpenAI API key just to
instantiate — directly contradicting a zero-infra assumption until explicitly
reconfigured. The adapter therefore ships **its own pinned local config**
(fastembed + chroma + `infer=False`), so that friction never reaches a user.
Evidence: [SPIKE-memory-mem0-wrap.md](../specs/archive/spikes/SPIKE-memory-mem0-wrap.md).

---

## 7. Package structure

```mermaid
graph TD
    subgraph fabrica["pip install fabrica"]
        Protocols["Protocols:<br/>ToolNamespace, Sandbox, SkillStore,<br/>MemoryStore, PromptStore, Retriever"]
        Defaults["Defaults:<br/>KeywordBackend (Rust+PyO3), subprocess<br/>sandbox, filesystem skill loader"]
    end
    subgraph contrib["fabrica-contrib[extra] — opt-in"]
        Sandboxes["[firecracker] [srt] [libkrun]<br/>[gvisor] [kata]"]
        Retrievers["[prx] [llamaindex] [langchain]"]
        Memories["[mem0] [zep] [letta] [cognee]"]
    end
    fabrica -->|"same interface,<br/>swap the backend"| contrib
```

**Engineering principle applied throughout:** where Fabrica *builds* compute
internals (like the default `KeywordBackend`), it's Rust with a Python binding —
matching prx's own shape — not pure Python. Wrapped libraries (Mem0, LlamaIndex,
prx itself) are unaffected. Detail: [context-layer.md](context-layer.md#engineering-principle-rust-for-compute-python-for-interface).

---

## 8. How it all closes the loop — one request, start to finish

```mermaid
sequenceDiagram
    participant U as User
    participant Ag as Agent (Civitas AgentProcess)
    participant Pr as Presidium
    participant Fb as Fabrica
    participant Sb as Sandbox

    U->>Ag: task requiring a tool + some data processing
    Ag->>Pr: is this agent allowed to run code-mode?
    Pr-->>Ag: ALLOW (grant check, budget check)
    Ag->>Fb: writes code against ToolNamespace
    Fb->>Sb: Sandbox.run(code) — platform-appropriate tier
    Sb-->>Fb: small final result only
    Fb-->>Pr: emits usage event (cpu-seconds, tool-call count)
    Fb-->>Ag: result
    Ag-->>U: answer
    Note over Pr: Presidium meters + can throttle —<br/>Fabrica never decides ALLOW/DENY itself.
```

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
