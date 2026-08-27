# Fabrica

**The context layer for Python agents.** Part of the [Civitas](https://github.com/civitas-io/python-civitas) platform.

[![PyPI](https://img.shields.io/pypi/v/fabrica-context)](https://pypi.org/project/fabrica-context/)
[![GitHub release](https://img.shields.io/github/v/release/civitas-io/fabrica)](https://github.com/civitas-io/fabrica/releases)

```bash
pip install fabrica-context               # import fabrica
pip install fabrica-context[presidium]    # + RestPresidiumClient (real REST+mTLS governance client)
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

![Where Fabrica sits — three pillars: Context (Fabrica), Control (Presidium), Runtime (Civitas)](assets/platform-layers.svg)

Civitas keeps agents alive. Presidium keeps them accountable. **Fabrica
decides what they see and how they act on it.**

## What it does

| Capability | Shape |
|---|---|
| **Tools as code** | A code-API namespace + isolated execution — the headline mechanism above. A `find()` retrieval fallback exists for models that don't do code-mode. |
| **Skills gateway** | Loads the open `SKILL.md` standard with progressive disclosure — validated against the real 81-skill `bigpowers` catalog. |
| **Memory** | A `MemoryStore` protocol wrapping mature backends (Mem0 today; Zep/Letta/Cognee planned) — working memory, compaction, and long-term recall as three separate facets, not one blended score. |
| **Prompts** | A versioned, addressable `PromptStore` — storage and retrieval only, deliberately not rendering or compression. |
| **MCP, both directions** | A real MCP client (`MCPToolNamespace`) and a real MCP server (`FabricaMCPServer`, stdio + HTTP) — external tools come in, Fabrica's own tools/skills/memory/prompts go out, without reintroducing the schema-dump cost code-mode exists to avoid. Client-side transports: `stdio`, `sse`, and `streamable_http` — see benchmarks below. |
| **Tunnels** | `TunnelProvider` for exposing a local dev deployment publicly — Tailscale Funnel and Cloudflare quick tunnels, both credential-free. |

## MCP transport benchmarks — real, not estimated

**Methodology, stated plainly**: single OS process, single OS thread, for
both client and server (concurrency = concurrent `anyio` tasks on one event
loop, not real parallelism); one shared `ClientSession`/connection for all
concurrent callers, not independent simulated users; loopback only, zero
real network hop; a trivial, zero-work `echo` tool, to isolate transport
overhead specifically. Real hardware (AMD Ryzen 9 3900X, 24 threads),
matched dependency versions (`mcp==2.0.0`, `uvicorn==0.51.0`), no mocks.

Compared against two independently-published, real industry benchmarks
(TM Dev Lab's cross-language benchmark, Stacklok/ToolHive's transport
benchmark) — the honest conclusion: no fair, direct ranking is possible from
this spike alone, since neither the network topology nor the tool workload
match. What DOES hold up: this implementation's raw transport overhead is
the same order of magnitude as other real Python MCP implementations once
those differences are accounted for, and its throughput-plateau finding is
independently corroborated by Stacklok's own session-scaling data. Full
methodology, raw JSON, every finding, and the full industry comparison with
real citations:
[`specs/archive/spikes/SPIKE-mcp-transport-benchmark.md`](https://github.com/civitas-io/fabrica/blob/main/specs/archive/spikes/SPIKE-mcp-transport-benchmark.md).

| Transport | p50 latency | p99 latency | throughput @ concurrency=10 | RSS growth / 2000 calls |
|---|---|---|---|---|
| `stdio` | 0.69ms | 0.71ms | 2356 calls/s | 0.00 MB |
| `sse` | 1.32ms | 1.61ms | 991 calls/s | 0.00 MB |
| `streamable_http` | 2.01ms | 2.68ms | 673 calls/s | 0.00 MB |

`streamable_http` runs at roughly 3x `stdio`'s p50 latency and ~1.5x `sse`'s —
expected, given it layers a full HTTP request/response on top of what `sse`
does over an already-open stream. No transport showed measurable memory
growth at 2000 calls. A real, notable finding: throughput did not
meaningfully scale past ~5 concurrent callers sharing one `ClientSession`,
on any transport — see the spike doc for the full reasoning and the (still
open) question of whether multiple sessions would scale differently.

## Isolation — the differentiator

Running model-generated code needs real isolation. Fabrica treats it as one
pluggable, tiered `Sandbox` protocol, auto-detected per host — callers never
choose a backend themselves.

![One Sandbox protocol, platform-detected at runtime: Tier 0 subprocess everywhere, Tier 1 SrtSandbox (validated on macOS, srt-documented-but-untested on Linux/Windows), Tier 2 FirecrackerSandbox on Linux+KVM with real opt-in snapshot/restore and jailer hardening](assets/isolation-tiers.svg)

Tier 2 (Firecracker) is the real production target on Linux: microVM-per-agent
isolation, cold-boot by default, with **opt-in** snapshot/restore (~8–11ms
warm restores, measured) and **opt-in** `jailer` hardening (chroot + cgroups +
uid/gid drop on top of the KVM boundary) — both validated end to end on real
hardware, not simulated. Full detail: [Isolation](isolation.md).

## Status

Alpha, real and tested, not just designed. Nine contracts, fifteen spikes (all real
hardware/API evidence — [`specs/archive/spikes/`](https://github.com/civitas-io/fabrica/tree/main/specs/archive/spikes/)),
every object-model component built and tested, not just designed:
`Retriever`, `Sandbox`/`SandboxPool` (all three tiers implemented), `ToolManager`,
`SkillManager`, `MemoryManager`, `PromptManager`, `CivitasBridge`, both MCP
directions, `TunnelProvider`, and -- as of v0.2.0 -- `RestPresidiumClient`, the real
REST+mTLS `PresidiumClient` implementation (circuit-breaker protected, fail-closed),
verified against [`civitas-io/presidium`](https://github.com/civitas-io/presidium)'s
own real, live M7 server, not mocks. A real GitHub Actions pipeline lints, tests,
and builds on every push.

Genuinely open, not hidden: managed-provider adapters (E2B/Modal/AWS/Azure/GCP)
are blocked on real cloud credentials this project doesn't have yet; a
`fabrica`/`fabrica-contrib` package split is designed but deliberately
deferred until a real adapter needs it. Full, current, ordered state:
[`HANDOFF.md`](https://github.com/civitas-io/fabrica/blob/main/HANDOFF.md) → [Plan](PLAN.md).

## Documentation

| Start here | For |
|---|---|
| [`HANDOFF.md`](https://github.com/civitas-io/fabrica/blob/main/HANDOFF.md) | Current state, what's done, what's next — read this first |
| [Architecture](architecture.md) | Visual tour of the whole system |
| [System Design](system-design.md) | Internals: object model, deployment topology, state ownership |
| [Contracts](contracts/managers.md) | Implementation-ready signatures for every component |
| [Plan](PLAN.md) | The active, ordered work queue |

Design docs are marked **Status: Design** — exploratory reasoning, may describe something not
yet built or since superseded. Contract docs are marked **Status: Contract —
implementation-ready** — exact types/signatures, ready to build against. Four topics have both
(MCP Integration, MCP Server, Memory, Prompts); each Design page links forward to its Contract
counterpart, and vice versa. See [`AGENTS.md`](https://github.com/civitas-io/fabrica/blob/main/AGENTS.md) for the
full precedence rule.

## License

Apache 2.0.
