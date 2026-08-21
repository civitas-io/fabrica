# Fabrica

**The context layer for Python agents.** Part of the [Civitas](https://github.com/civitas-io/python-civitas) platform.

```
pip install fabrica-context   # import fabrica
```

> `fabrica` itself is already taken on PyPI by an unrelated project — the
> project and repo keep the name "Fabrica", only the distribution name differs
> (same pattern as `pip install pyyaml` → `import yaml`).

---

## What this is

Agents don't fail because the model is weak — they fail because the **context
window is mismanaged**. Every tool schema, every skill, every memory, every
intermediate result gets stuffed into the prompt whether the model needs it or
not, and both cost and accuracy degrade as a result.

Fabrica is the answer: it decides **what an agent sees and where its code
actually runs**. Its headline mechanism is **code-mode** — a model writes code
against a tool namespace instead of making individual tool calls, that code
runs in an isolated sandbox, and only the final result crosses back into the
model's context. Measured on real hardware, not claimed:
[~79% cheaper and more correct](https://github.com/civitas-io/fabrica/blob/main/specs/archive/spikes/SPIKE-code-mode-execution.md)
than traditional tool-calling.

![Where Fabrica sits — three pillars: Context (Fabrica), Control (Presidium), Runtime (Civitas)](https://raw.githubusercontent.com/civitas-io/fabrica/main/docs/assets/platform-layers.svg)

Civitas keeps agents alive. Presidium keeps them accountable. **Fabrica
decides what they see and how they act on it.**

## What it does

| Capability | Shape |
|---|---|
| **Tools as code** | A code-API namespace + isolated execution — the headline mechanism above. A `find()` retrieval fallback exists for models that don't do code-mode. |
| **Skills gateway** | Loads the open `SKILL.md` standard with progressive disclosure — validated against the real 81-skill `bigpowers` catalog. |
| **Memory** | A `MemoryStore` protocol wrapping mature backends (Mem0 today; Zep/Letta/Cognee planned) — working memory, compaction, and long-term recall as three separate facets, not one blended score. |
| **Prompts** | A versioned, addressable `PromptStore` — storage and retrieval only, deliberately not rendering or compression. |
| **MCP, both directions** | A real MCP client (`MCPToolNamespace`) and a real MCP server (`FabricaMCPServer`, stdio + HTTP) — external tools come in, Fabrica's own tools/skills/memory/prompts go out, without reintroducing the schema-dump cost code-mode exists to avoid. |
| **Tunnels** | `TunnelProvider` for exposing a local dev deployment publicly — Tailscale Funnel and Cloudflare quick tunnels, both credential-free. |

## Isolation — the differentiator

Running model-generated code needs real isolation. Fabrica treats it as one
pluggable, tiered `Sandbox` protocol, auto-detected per host — callers never
choose a backend themselves.

![One Sandbox protocol, platform-detected at runtime: Tier 0 subprocess everywhere, Tier 1 SrtSandbox (validated on macOS, srt-documented-but-untested on Linux/Windows), Tier 2 FirecrackerSandbox on Linux+KVM with real opt-in snapshot/restore and jailer hardening](https://raw.githubusercontent.com/civitas-io/fabrica/main/docs/assets/isolation-tiers.svg)

Tier 2 (Firecracker) is the real production target on Linux: microVM-per-agent
isolation, cold-boot by default, with **opt-in** snapshot/restore (~8–11ms
warm restores, measured) and **opt-in** `jailer` hardening (chroot + cgroups +
uid/gid drop on top of the KVM boundary) — both validated end to end on real
hardware, not simulated. Full detail: [`docs/isolation.md`](https://github.com/civitas-io/fabrica/blob/main/docs/isolation.md).

## Status

Alpha, real and tested, not just designed. Nine contracts, fifteen spikes (all real
hardware/API evidence — [`specs/archive/spikes/`](https://github.com/civitas-io/fabrica/tree/main/specs/archive/spikes/)),
every object-model component built and tested, not just designed:
`Retriever`, `Sandbox`/`SandboxPool` (all three tiers implemented), `ToolManager`,
`SkillManager`, `MemoryManager`, `PromptManager`, `CivitasBridge`, both MCP
directions, and `TunnelProvider`. A real GitHub Actions pipeline lints, tests,
and builds on every push.

Genuinely open, not hidden: managed-provider adapters (E2B/Modal/AWS/Azure/GCP)
are blocked on real cloud credentials this project doesn't have yet; a
`fabrica`/`fabrica-contrib` package split is designed but deliberately
deferred until a real adapter needs it. Full, current, ordered state:
[`HANDOFF.md`](https://github.com/civitas-io/fabrica/blob/main/HANDOFF.md) → [`docs/PLAN.md`](https://github.com/civitas-io/fabrica/blob/main/docs/PLAN.md).

## Documentation

| Start here | For |
|---|---|
| [`HANDOFF.md`](https://github.com/civitas-io/fabrica/blob/main/HANDOFF.md) | Current state, what's done, what's next — read this first |
| [`docs/architecture.md`](https://github.com/civitas-io/fabrica/blob/main/docs/architecture.md) | Visual tour of the whole system |
| [`docs/system-design.md`](https://github.com/civitas-io/fabrica/blob/main/docs/system-design.md) | Internals: object model, deployment topology, state ownership |
| [`docs/contracts/`](https://github.com/civitas-io/fabrica/tree/main/docs/contracts/) | Implementation-ready signatures for every component |
| [`docs/PLAN.md`](https://github.com/civitas-io/fabrica/blob/main/docs/PLAN.md) | The active, ordered work queue |

Discovery, design, and validation docs (personas, landscape research, per-
component design docs, spike write-ups) live under [`docs/`](https://github.com/civitas-io/fabrica/tree/main/docs/) and
[`specs/archive/spikes/`](https://github.com/civitas-io/fabrica/tree/main/specs/archive/spikes/) — grouped and indexed in
`HANDOFF.md`'s own reading-order section.

## License

Apache 2.0.
