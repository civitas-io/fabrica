# Fabrica ↔ Civitas ↔ Presidium

**Status:** Design · **Last updated:** 2026-08

---

## One-line separation

> **Civitas:** run agents reliably. · **Presidium:** run agents accountably. ·
> **Fabrica:** shape what agents see and where their work runs.

Additive, never competing. Fabrica is meaningless without Civitas (it needs the
process substrate) and is *governed by* Presidium (which decides what it may do).

## Where Fabrica sits

```
                 ┌─────────────────────────────┐
   model ◄──────►│  Fabrica  (CONTEXT)         │
  context        │  tools-as-code · skills ·    │
   window        │  memory · prompts · sandbox  │
                 └───────────┬─────────────────┘
        grants/policy/creds  │  spans/audit events
                 ┌───────────▼─────────────────┐
                 │  Presidium (CONTROL)         │
                 └───────────┬─────────────────┘
                             │  ModelProvider/ToolProvider/AuditSink/creds
                 ┌───────────▼─────────────────┐
                 │  Civitas   (RUNTIME)         │
                 │  supervision · bus · OTEL    │
                 └─────────────────────────────┘
```

## What Fabrica consumes from Civitas

| Civitas seam | Fabrica use |
|---|---|
| `AgentProcess` / `GenServer` | run the sandbox pool + skill/memory services as supervised children |
| Supervision tree | a crashed sandbox pool restarts automatically |
| `ToolProvider` / `MCPClient` | one `ToolSource` for the tool namespace / `find_tools` |
| `StateStore` | persistence for the default memory + prompt stores |
| OTEL tracing | every tool call, code-mode run, skill load, memory op emits a span |
| Transport / bus | service-mode components addressable fleet-wide |

Fabrica adds **no new runtime primitives.** It is a set of supervised services + a
library, built on Civitas as-is.

## What Fabrica exposes to Presidium (governance seams)

Presidium is the policy authority; Fabrica enforces at the execution boundary:

| Decision | Presidium provides | Fabrica enforces |
|---|---|---|
| May this agent load/call this tool? | grant check (`tool:...`) | filters the tool namespace bound into the sandbox |
| May this agent run code mode at all? | policy (ALLOW/DENY/REQUIRE_APPROVAL) | refuses/queues the `Sandbox.run` |
| May this skill be loaded/run? | grant + poisoning check | `SkillStore.load/run` gate |
| May this memory scope be read? | grant (`data:...`) | `MemoryStore` scope filter |
| Secrets for a tool/sandbox | credential context injection | injects into the sandbox so code *uses* but never *sees* them |
| Human approval for a run | durable suspension + HITL resume | pauses the run, resumes on approval |

These reuse Presidium's existing eight integration points with Civitas — Fabrica does
not invent a parallel governance path.

## The tessera seam (credentials in the sandbox)

Code mode's sandbox needs real credentials to do real work, but the model must never
see them. This is precisely **tessera's** model (agent-blind credential broker), and
Presidium's credential-vault concern at runtime.

- **In production runtime:** Presidium's credential path injects scoped secrets into
  the Firecracker microVM over `vsock`; the generated code calls tools that use them;
  plaintext never crosses back to the model context.
- **At the CLI (coding agents):** tessera does the same for terminal tools.

Fabrica designs the sandbox so injected secrets live only inside the microVM and are
scrubbed from any result returned to the model. **tessera stays a separate product;**
this is a designed seam, not a merge (see the toolchain note below).

## Usage & budget ceilings — metering vs. enforcement

Two different jobs get bundled under "usage tracking," and they split across the
boundary exactly like everything else in this doc:

- **Metering** (recording consumption) — Fabrica's job, for anything it executes.
- **Enforcement** (ALLOW/DENY/THROTTLE once a ceiling is hit) — Presidium's job, as
  a direct extension of the cost-tracking/budget-enforcement scope it already claims
  for the LLM Gateway.

**This is not a fourth system.** What's new is that consumption isn't only LLM
tokens anymore — Fabrica introduces dimensions the LLM gateway was never built to
see (sandbox compute-seconds, tool-call counts, memory volume, skill invocations).
Rather than Fabrica building its own budget system, it emits standardized
consumption events into the same ledger Presidium's LLM gateway already writes to,
so a session/user/team has **one** budget, not two disconnected ones.

### What Fabrica emits

| Component | Consumption events |
|---|---|
| `Sandbox` ([isolation.md](isolation.md)) | cpu-seconds, wall-clock duration, memory bytes — per run |
| `ToolNamespace` / `find_tools` ([tool-execution.md](tool-execution.md)) | call count, latency — per tool per call |
| `MemoryStore` ([memory.md](memory.md)) | read/write volume — per scope |
| `SkillStore` ([skills-gateway.md](skills-gateway.md)) | invocation count — per skill |

Every event carries the same `Scope` already used by `MemoryStore` — **extended to
include `team_id`**, since per-team ceilings aren't covered by the original
user/session/agent shape:

```python
@dataclass
class Scope:
    user_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    team_id: str | None = None   # added for usage/budget rollups
```

### What Fabrica does NOT do

- Does not aggregate consumption into a ledger.
- Does not decide whether a session/team is over budget.
- Does not throttle or deny on its own initiative.

It only **checks before executing**: if Presidium's policy engine already flags a
scope as over-budget — returned the same way as any other ALLOW/DENY/
REQUIRE_APPROVAL decision — Fabrica refuses the run *before* it starts. Same
pattern as every other governance seam in this doc; no new enforcement path invented.

### Deployment shape

Same pattern as everywhere else in the platform: **library mode** (in-process
counters — enough for single-deployment ceilings) vs. **service mode** (a shared
`UsageLedger` service, e.g. backed by Postgres/Redis, addressable across a fleet for
centralized cross-team ceilings). Same interface either way; Fabrica doesn't care
which mode Presidium runs it in.

## What stays out of Fabrica

- Generic MCP proxy/registry → commoditized infra (see landscape.md).
- Governed LLM/MCP gateway → Presidium (wraps agentgateway).
- prx / tessera → separate Rust "agent toolchain" for coding agents at the CLI;
  different language, consumer, and distribution. Shared *ideas* (semantic search;
  agent-blind secrets), not shared code.

## Rollout without breaking anything

Fabrica currently lives as a package inside `civitas-contrib`. Plan:

1. Land these docs in a standalone `civitas-io/fabrica` repo (thesis first).
2. Build `fabrica` (protocols + defaults) depending only on `civitas`.
3. Add `fabrica-contrib` extras (firecracker/gvisor/mem0/... ) as they're built.
4. Deprecate the `civitas-contrib/packages/fabrica` stub in favour of the new repo;
   keep `civitas-contrib` for pure runtime adapters (providers, state stores).
5. Fold or co-brand `promptshrink` (context compression) under the Fabrica family.
