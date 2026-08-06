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

**Why `ToolManager` and `SkillManager` stay separate classes, not one generic
manager:** they look similar (both register into `Retriever`, both run things in
`SandboxPool`) but differ in ways that matter — `ToolManager` registers
developer-authored namespaces and executes *arbitrary, freshly-generated* code;
`SkillManager` parses the `SKILL.md` file format and executes *pre-written,
author-trusted* scripts by name with structured args. Forcing one class to cover
both would mean branching on `kind` inside what should be a clean abstraction.
**What they do share — composition, not inheritance:** the orchestration around
execution (check grant → acquire sandbox → run → release → handle errors → emit
spans) is identical regardless of what's being executed. Both call a single
shared helper, `execute_in_sandbox(presidium_client, sandbox_pool, action, ...)`,
for that part — the same dependency-injection pattern `Retriever` and
`SandboxPool` already use, extended to the one piece of logic that was actual
copy-paste risk, without conflating two different trust models into one class.

**Why `CivitasBridge` and `PresidiumClient` are separate objects**, not methods
on `Fabrica` itself: they're the only two places this system talks to something
outside itself. Isolating them means every outbound call — to the runtime, to
governance — goes through one seam, which is where mocking, circuit-breaking, and
the fail-closed behavior in §6 all live in one place instead of scattered across
four managers.

**`PresidiumClient` is deliberately smaller than it first looks.** Presidium is a
genuinely separate deployment (not co-located with Civitas), reached over REST +
mTLS — so `PresidiumClient` has exactly **one** method: `check_grant`/`check_policy`,
synchronous (you can't proceed without knowing ALLOW/DENY), circuit-breaker
protected, fail-closed on timeout or an open breaker. There is **no**
`emit_usage_event` method. Usage reaches Presidium as attributes on the OTEL spans
Fabrica already emits for its own observability (§7) — async by construction
(OTEL's exporter batches in the background), reusing existing plumbing instead of
building a second one. Presidium implements its own span consumer watching for
`fabrica.*` spans; that consumer is Presidium's concern, not something
`PresidiumClient` calls out to.

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
would break the whole token-savings story from
[SPIKE-code-mode-execution.md](../specs/archive/spikes/SPIKE-code-mode-execution.md).

**The callback transport, resolved:** a shared wire protocol (ZMQ) over a
tier-specific bridge — not one uniform transport, and not a different RPC
implementation per tier either:

- **Tier 0/1** (no real VM boundary — subprocess, gVisor, `srt`): ZMQ binds
  directly via `ipc://` (Unix domain socket). No relay needed — guest and host
  share a kernel.
- **Tier 2** (real VM boundary — Firecracker, libkrun, eventually Hyper-V): a
  small relay, baked into the guest image the same way the tool-namespace shim
  already is, speaks ZMQ locally to the sandbox-side shim and bridges those
  bytes across the actual platform-specific channel — `vsock` for Firecracker,
  `VZVirtioSocketDevice` for libkrun, `AF_HYPERV` for Hyper-V — to a matching
  bridge on the host side, which re-exposes ZMQ to `ToolManager`.

This means `ToolManager` and the sandbox-side shim speak **only ZMQ, always**
— tier/platform-specific complexity collapses into one small, isolated relay
component instead of being spread across the application-level RPC code. It
may also let Fabrica reuse Civitas's own ZMQ transport implementation (already
part of its scaling ladder) rather than hand-rolling a second one. The relay is
trusted, Fabrica-controlled infrastructure the generated code never touches
directly — the same trust boundary as the tool-namespace shim already
described in `isolation.md`.

**Sanity-checked:** [SPIKE-zmq-sandbox-channel-feasibility.md](../specs/archive/spikes/SPIKE-zmq-sandbox-channel-feasibility.md)
confirmed Tier 0/1's half of this — `pyzmq` bundles its own statically-compiled
`libzmq` (1.6MB, self-contained, no system dependency chain needed), and a real
`ipc://` round trip measured **0.73ms**, negligible overhead. **Still not
verified:** the Tier 2 relay bridge itself (`vsock`/`VZVirtioSocketDevice`/
`AF_HYPERV`) and behavior inside an actual booted guest rather than the same
OS/arch on bare metal — the harder half of this design, deliberately left for
its own dedicated spike rather than attempted alongside the easier half.

---

## 4. Component responsibility matrix

| Component | Owns | Depends on | Library mode | Service mode |
|---|---|---|---|---|
| `Fabrica` | top-level config, wiring | all managers | plain object | plain object (always — it's the entry point, never itself a GenServer) |
| `ToolManager` | `ToolNamespace` registration, code-mode orchestration | `Retriever`, `SandboxPool`, `PresidiumClient`, shared `execute_in_sandbox` helper | in-process | GenServer |
| `SkillManager` | `SKILL.md` loading, skill execution orchestration | `Retriever`, `SandboxPool`, `PresidiumClient`, shared `execute_in_sandbox` helper | in-process | GenServer |
| `MemoryManager` | adapter lifecycle (Mem0 etc.) | configured `MemoryStore` adapter | in-process | GenServer, shared across a fleet |
| `PromptManager` | `PromptStore` | Civitas `StateStore` | in-process | GenServer |
| `Retriever` | index, search | `KeywordBackend` (default) or an adapter | in-process, local index | GenServer, one shared index fleet-wide |
| `SandboxPool` | tier selection, pool of handles, platform dispatch, **baking the guest image** (tool-namespace shim +, for Tier 2, the ZMQ relay) | a `Sandbox` backend (subprocess/gVisor/Firecracker/`srt`/libkrun) | in-process, small pool | GenServer, supervised, larger warm pool |
| `CivitasBridge` | mode selection at construction time | Civitas `Runtime` | no-op | registers GenServers with the supervision tree |
| `PresidiumClient` | grant/policy checks only — no usage-emission method | Presidium's REST endpoint (mTLS) | same: REST + mTLS, circuit-breaker protected (Presidium is always a separate deployment, not affected by Fabrica's own mode) |

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
| Presidium unreachable | REST timeout on `check_grant`, or an open circuit breaker after N consecutive failures | **fail closed — DENY by default.** Never fail-open on a security check. This is a real, explicit availability-vs-safety tradeoff: a Presidium outage degrades Fabrica to doing nothing, on purpose. The circuit breaker means this triggers immediately once tripped, not after a fresh timeout wait on every call — and its cooldown/half-open retry protects Presidium from a thundering herd the moment it recovers. |
| Warm pool exhausted | `acquire()` finds no available handle | **Resolved — hybrid bounded overflow** (see §7): cold-start on demand up to a hard `max_concurrent` ceiling; only queue (bounded wait + timeout, structured error if it expires) once that ceiling is hit. Never unbounded, never queues while the host still has headroom. |
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

**These spans do double duty.** Beyond tracing, they are also how usage reaches
Presidium (see §1) — which means every span above that has a resource-consumption
attribute (`fabrica.sandbox.run`'s `cpu_seconds`, `fabrica.tool.code_mode.run`'s
`tool_call_count`, etc.) must also carry `Scope` (`user_id`/`session_id`/`agent_id`/
`team_id`) as span attributes, so Presidium's span consumer can attribute
consumption to the correct budget scope. This is a real, small addition these
spans didn't need before — not an assumption already covered elsewhere.

---

## What this doc deliberately leaves open

Real decisions, not oversights — surfaced here so contracts work starts from a
known list, not silent assumptions:

1. ~~Warm-pool-exhausted behavior~~ **Resolved.** Two config values on `SandboxPool`:
   `warm_size` (pre-booted, ready) and `max_concurrent` (hard ceiling, warm +
   cold-started combined). `acquire()` tries the warm pool first (8–11ms); if
   empty and under `max_concurrent`, cold-starts on demand (bounded, ~1s per the
   Firecracker spike); only once `max_concurrent` is hit does it queue with a
   bounded timeout, returning a structured error if the timeout expires — never
   a silent hang. Chosen over unbounded cold-start specifically because it's the
   direct answer to Marcus's own stated fear: *"a bad run can't touch the host or
   blow the budget... a runaway resource consumer"* — unbounded cold-start under
   a traffic spike or adversarial burst is exactly that shape. Refinement, not a
   separate decision: a cold-started overflow sandbox that finishes work is
   folded back into the warm pool (if under `warm_size`) rather than discarded —
   bursts organically regrow the warm pool instead of needing manual resizing.
   Exact queue-timeout duration is left as an operator-tunable config value, not
   hardcoded here — it's a deployment SLA choice, not an architecture one.
2. ~~`PresidiumClient`'s exact transport~~ **Resolved.** REST + mTLS, since
   Presidium is a genuinely separate deployment, not co-located with Civitas.
   Circuit-breaker protected. Only one method exists (`check_grant`) —
   `emit_usage_event` was removed entirely, not made async, because usage already
   rides the OTEL spans Fabrica emits for its own observability (§7). Async
   preference was satisfied by reusing existing plumbing, not building new plumbing.
3. ~~The callback transport~~ **Resolved** (see §3): shared ZMQ wire protocol,
   direct `ipc://` for Tier 0/1 (no relay needed), a small guest-side relay
   bridging to the real platform-specific channel (`vsock` / `VZVirtioSocketDevice`
   / `AF_HYPERV`) for Tier 2. Implementation feasibility (e.g. `libzmq` inside a
   minimal Firecracker guest) still needs a sanity-check spike — the
   architecture is decided, the build artifact isn't yet proven.
4. ~~Should `ToolManager` and `SkillManager` be one generic class?~~ **Resolved —
   composition, not inheritance, and not a merge.** They stay separate classes:
   their loading (`SKILL.md` parsing vs. namespace registration) and trust models
   (author-trusted named scripts vs. freshly-generated arbitrary code) genuinely
   differ. What's actually shared — the execute-in-sandbox orchestration — is one
   small helper both call, not a base class both inherit.
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
