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
   Headline: tools-as-code + sandboxed execution. Fallback: `find_tools` retrieval.
2. **Skills** — packaged, reusable agent capabilities, loaded on demand, conformant
   to the open `SKILL.md` standard.
3. **Memory** — session and long-term recall, via a protocol with adapters for
   mature backends (not a reimplementation).
4. **Prompts** — versioned, addressable prompt management.
5. **Isolation** — the sandbox substrate that tool/skill code executes in, tiered from
   subprocess to microVM.

## Scope: what Fabrica does NOT own

Keep the pillar boundaries clean, exactly as Civitas/Presidium do:

| Not Fabrica's concern | Who owns it | Why |
|---|---|---|
| Process lifecycle, supervision, transport | Civitas | runtime primitive |
| OTEL span plumbing | Civitas | Fabrica *emits*, Civitas *collects* |
| Policy: ALLOW/DENY of a tool or code run | Presidium | governance decision |
| Grants: which tools/skills an agent may touch | Presidium | authorization |
| Credential vault / token exchange | Presidium (+ tessera at CLI) | governance |
| A generic MCP proxy/registry | nobody — it's commoditized infra | see landscape.md |
| Governed LLM/MCP gateway | Presidium (wraps agentgateway) | governance |

Fabrica shapes and runs context. It does not run the process (Civitas) or decide
permission (Presidium). It **emits** the spans and audit events those layers consume.

## Interface-first, mirroring the platform

```
fabrica/            # protocols + lightweight defaults, depends only on civitas
  ToolNamespace, Sandbox, SkillStore, MemoryStore, PromptStore  (protocols)
  find_tools fallback, subprocess sandbox, filesystem skill loader  (defaults)

fabrica-contrib/    # adapters, opt-in extras
  [firecracker] [gvisor] [kata]        # sandbox backends
  [mem0] [zep] [letta] [cognee]        # memory backends
  [mcp]                                # MCP tool source
  [search]                             # embedding retrieval fallback
```

`pip install fabrica` gives you working defaults with zero infrastructure.
`fabrica-contrib[firecracker]` (etc.) upgrades a single component for production —
same interface, no agent-code changes. This is the exact contract Civitas and
Presidium already ship.

## Deployment modes

Like the rest of the platform, every component runs in two modes:

- **Library mode** — in-process inside a Civitas deployment. Zero infra. Dev + small.
- **Service mode** — a supervised `GenServer` on the Civitas bus (e.g. a shared
  sandbox pool or memory service). Same interface, shared state, central management.

## Why this framing wins

- **Symmetry sells.** "Runtime, Control, Context — Civitas, Presidium, Fabrica" is a
  story an enterprise architect can hold in their head.
- **It's defensible.** The commoditized parts (generic gateways) are explicitly *not*
  Fabrica. The defensible parts (vendor-neutral sandboxed execution on a supervised
  runtime; runtime-native skills) *are*.
- **It absorbs the scattered ideas** (RFC 0001 tool retrieval, prompt library, skills
  gateway, promptshrink compression) into one coherent product instead of four
  half-products.
