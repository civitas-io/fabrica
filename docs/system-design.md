# System Design

**Status:** Design, pre-contracts · **Last updated:** 2026-08
**Precedes:** contracts (exact signatures, error types, async behavior)
**Follows:** [architecture.md](architecture.md) (what & why) — this doc is the how

This is the layer between "what Fabrica is" (`architecture.md`) and "what code do
I write" (contracts, not yet written). It answers questions architecture.md
deliberately left as boxes-and-arrows: what are the actual Python objects, who
owns what state, what happens when something fails, what gets traced.

---

## 1. Internal object model

What `from fabrica import Fabrica` actually gives you — one facade, four domain
managers, two shared engines injected into the managers that need them, and two
bridge objects that connect outward to Civitas and Presidium.

![Internal object model: Fabrica facade, ToolManager, SkillManager, MemoryManager, PromptManager, shared Retriever and SandboxPool, CivitasBridge and PresidiumClient](assets/internal-object-model.svg)

**Why `Retriever` and `SandboxPool` are shared, singular instances**, not one per
manager: `retrieval.md`'s whole thesis is that tools and skills use *the same*
engine (that's what fixed the O(1)-vs-linear gap). Duplicating `Retriever`
per-manager would silently reintroduce the bug that unification fixed. Same logic
for `SandboxPool` — one pool, drawn from by both code-mode tool execution and
skill script execution, not two pools competing for the same host resources.

**Why `CivitasBridge` and `PresidiumClient` are separate objects**, not methods
on `Fabrica` itself: they're the only two places this system talks to something
outside itself. Isolating them means every outbound call — to the runtime, to
governance — goes through one seam, which is where mocking, circuit-breaking, and
the fail-closed behavior in §6 all live in one place instead of scattered across
four managers.

---

## 2. Deployment topology: library mode vs. service mode

Every manager, plus `Retriever` and `SandboxPool`, works both ways. The interface
above doesn't change — only what's underneath it.

![Two deployment modes: library mode (plain objects, SQLite, function calls) vs service mode (GenServers, Postgres/Redis, message bus)](assets/deployment-topology.svg)

**This isn't a spectrum — it's a binary per-deployment choice**, matching how
Civitas and Presidium already work. A team doesn't run "70% service mode"; they
run library mode until they outgrow it, then flip to service mode, same interface,
zero application-code changes. `CivitasBridge`'s job (§1) is entirely this: decide
once, at construction time, which shape each manager takes.

---

## 3. Internal flow: one code-mode call, in detail

`architecture.md §3` and `§8` show this from the outside (model↔Fabrica, then
user↔agent↔Fabrica). This is what happens **inside** Fabrica for a single call —
including a detail neither of those diagrams shows: the sandbox doesn't have
magic access to real tools. It calls back into `ToolManager` for every real tool
invocation the generated code makes.

![Internal sequence: Agent to ToolManager to PresidiumClient to SandboxPool to Sandbox, with the callback loop for real tool calls](assets/internal-code-mode-flow.svg)

**The callback loop (step 9) is the load-bearing detail.** It's *why*
intermediate data never leaves the sandbox: the generated code calls
`namespace.call(tool, params)`, that call crosses back to `ToolManager` (which
actually has network/filesystem/API access), the real result crosses back in —
but only the *next* line of generated code sees it, not the model. This is the
mechanism, not a diagram simplification. Getting the callback transport wrong
(§7, open question) would break the whole token-savings story from
[SPIKE-code-mode-execution.md](../specs/archive/spikes/SPIKE-code-mode-execution.md).

---

## 4. Component responsibility matrix

| Component | Owns | Depends on | Library mode | Service mode |
|---|---|---|---|---|
| `Fabrica` | top-level config, wiring | all managers | plain object | plain object (always — it's the entry point, never itself a GenServer) |
| `ToolManager` | `ToolNamespace` registration, code-mode orchestration | `Retriever`, `SandboxPool`, `PresidiumClient` | in-process | GenServer |
| `SkillManager` | `SKILL.md` loading, skill execution orchestration | `Retriever`, `SandboxPool`, `PresidiumClient` | in-process | GenServer |
| `MemoryManager` | adapter lifecycle (Mem0 etc.) | configured `MemoryStore` adapter | in-process | GenServer, shared across a fleet |
| `PromptManager` | `PromptStore` | Civitas `StateStore` | in-process | GenServer |
| `Retriever` | index, search | `KeywordBackend` (default) or an adapter | in-process, local index | GenServer, one shared index fleet-wide |
| `SandboxPool` | tier selection, pool of handles, platform dispatch | a `Sandbox` backend (subprocess/gVisor/Firecracker/`srt`/libkrun) | in-process, small pool | GenServer, supervised, larger warm pool |
| `CivitasBridge` | mode selection at construction time | Civitas `Runtime` | no-op | registers GenServers with the supervision tree |
| `PresidiumClient` | grant checks, usage-event emission | Presidium's exposed interface | direct call if Presidium is in-process | call over the Civitas message bus |

---

## 5. State & persistence

| Component | State | Library mode | Service mode |
|---|---|---|---|
| `Retriever` index | tool/skill `Indexable`s | in-memory dict / BM25 | Postgres or Redis, shared |
| `SandboxPool` | warm handles, snapshot refs | local files / OS processes | shared node pool, snapshot store on disk or object storage |
| `MemoryManager` | conversation memories | SQLite + local vector (fastembed/chroma — the config [SPIKE-memory-mem0-wrap.md](../specs/archive/spikes/SPIKE-memory-mem0-wrap.md) validated) | hosted vector store, or a Postgres-backed adapter |
| `PromptManager` | prompt versions | local files or SQLite | Civitas `StateStore` / Postgres |
| Usage/budget counters | **not owned by Fabrica at all** | emitted to Presidium, not stored here | emitted to Presidium, not stored here |

That last row matters: per
[civitas-presidium-integration.md](civitas-presidium-integration.md#usage--budget-ceilings--metering-vs-enforcement),
Fabrica meters, Presidium owns the ledger. This table is a reminder that "state
Fabrica owns" and "state Fabrica reports on" are different rows, not the same one.

---

## 6. Error handling & resilience

Genuinely new content — none of the design docs so far specify what happens when
something breaks. Six real decisions, one flagged as a real availability tradeoff:

| Failure | Detection | Fabrica's response |
|---|---|---|
| Sandbox crashes mid-run | Civitas supervisor detects process death | `ToolManager` returns a structured error to the caller, not a silent hang. `SandboxPool` discards the handle and provisions a fresh one. **Civitas restarts the supervisor's child; Fabrica does not reimplement supervision.** |
| `Retriever` backend (e.g. prx) unreachable | persistent-process health check fails (same pattern validated in [SPIKE-prx-invocation-latency.md](../specs/archive/spikes/SPIKE-prx-invocation-latency.md)) | falls back to `KeywordBackend` automatically, logs a degraded-mode event. Never fails the caller outright for this. |
| Presidium unreachable | RPC timeout on `check_grant` | **fail closed — DENY by default.** Never fail-open on a security check. This is a real, explicit availability-vs-safety tradeoff: a Presidium outage degrades Fabrica to doing nothing, on purpose. |
| Warm pool exhausted | `acquire()` finds no available handle | **Open question — not decided here.** Queue with a bounded wait, or cold-start on demand (accepting the Firecracker cold-boot cost the earlier spike measured at ~1s)? See §7. |
| Generated code hangs | `Sandbox.run(..., timeout=...)` | hard timeout enforced by the sandbox itself; process/VM killed; a `TimedOut` error returned, not a hang. |
| Memory backend fails to instantiate (e.g. missing local model files) | at `MemoryManager` construction, not first use | **fail fast at Fabrica startup**, with a clear error — not a confusing failure mid-agent-task later. |

---

## 7. Observability: spans this system emits

| Component | Span | Key attributes |
|---|---|---|
| `ToolManager` | `fabrica.tool.find` | query, kind, result_count |
| `ToolManager` | `fabrica.tool.code_mode.run` | agent_id, code_hash, duration_ms, tool_call_count |
| `SandboxPool` | `fabrica.sandbox.acquire` | tier, warm_hit, wait_ms |
| `SandboxPool` | `fabrica.sandbox.run` | tier, duration_ms, cpu_seconds, exit_status |
| `Retriever` | `fabrica.retriever.search` | backend, query, limit, top_rank |
| `MemoryManager` | `fabrica.memory.write` / `fabrica.memory.search` | scope fields, backend |
| `PresidiumClient` | `fabrica.presidium.check_grant` | decision, latency_ms |

All spans ride Civitas's existing OTEL plumbing (`civitas-presidium-integration.md`
— "Fabrica emits, Civitas collects") — this table is what Fabrica emits, not a new
tracing mechanism.

---

## What this doc deliberately leaves open

Real decisions, not oversights — surfaced here so contracts work starts from a
known list, not silent assumptions:

1. **Warm-pool-exhausted behavior** (§6) — queue-with-timeout vs. cold-start.
2. **`PresidiumClient`'s exact transport** when Presidium isn't in-process — Civitas
   message bus, or a separate call? Affects the fail-closed timeout's real latency.
3. **The callback transport** (§3, step 9) — is it `vsock` uniformly (matching
   Firecracker's real mechanism from `isolation.md`), or does each tier
   (subprocess/Firecracker/`srt`/libkrun) implement its own? This one is load-bearing,
   not cosmetic — get it wrong and the token-savings story breaks.
4. **Should `ToolManager` and `SkillManager` actually be separate classes?** They
   have near-identical shapes (both use `Retriever` and `SandboxPool` the same way).
   Worth asking directly before contracts lock in two classes that should be one
   generic one.
5. **Mode-switching granularity** — one flag for the whole `Fabrica` instance, or
   can `Retriever` be service-mode while `SandboxPool` stays library-mode? The
   diagrams above assume all-or-nothing; that's an assumption, not a decision.

---

## Where to go next

| If you want... | Go to |
|---|---|
| The external, product-facing view | [architecture.md](architecture.md) |
| Retrieval engine detail | [retrieval.md](retrieval.md) |
| Isolation tier detail | [isolation.md](isolation.md) |
| Every claim checked against evidence | [critique.md](critique.md) |
