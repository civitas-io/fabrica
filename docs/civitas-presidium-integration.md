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
