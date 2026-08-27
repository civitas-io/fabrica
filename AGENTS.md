# AGENTS.md

**Package:** `fabrica` (import) | **Distribution:** `fabrica-context` (PyPI) | **Python:** ≥3.12

This file is a thin router for AI coding agents working in this repo. Its only job is
orientation — where things live and which doc wins when two disagree. Reference content
(API shapes, design reasoning) lives in `docs/`, not here; duplicating it here would just
create a second copy to drift, which is exactly the failure this file exists to prevent.

## What Fabrica is

The context layer for Python agents — part of the Civitas platform. Civitas keeps agents
alive (`civitas-io/python-civitas`), Presidium keeps them accountable (`civitas-io/presidium`),
Fabrica decides what they see and where their code runs: tool access (code-mode + `find()`
fallback), skills (`SKILL.md`), memory, prompts, and tiered sandboxing. See
[`README.md`](README.md) for the full pitch and real, measured numbers.

## Where things live — read this before trusting any doc in `docs/`

`docs/` has two tiers, distinguished by **directory**, not just a header line:

| Tier | Location | Meaning |
|---|---|---|
| **Design** | `docs/*.md` | Exploratory reasoning — problem, goals, alternatives. May describe something not yet built, or since superseded. Not implementation-ready by itself. |
| **Contract** | `docs/contracts/*.md` | Implementation-ready — exact types/signatures, ready to build against or already migrated from real code. |

**Four filenames exist in both directories** (`mcp-integration.md`, `mcp-server.md`,
`memory.md`, `prompts.md`) — genuinely different content, not duplicates. **The rule:
`docs/contracts/X.md` wins over `docs/X.md` whenever both exist** — the Contract file is
authoritative for exact API shape, the Design file for the reasoning behind it. Each of the
four Design docs links forward to its Contract counterpart (`**Formalized by:**`) and each
Contract doc links back (`**Depends on:**`) — `tests/test_docs_structure.py` enforces both
links exist, so this can't silently rot the way it did before 2026-08-27 (the Design→Contract
direction was missing until this file's own audit added it).

Contract docs without a same-named Design doc (`civitas-bridge.md`, `managed-sandbox.md`,
`managers.md`, `retriever.md`, `sandbox.md`) aren't collisions — they formalize a design that
either lives under a different name (e.g. `retriever.md` formalizes `retrieval.md`) or spans
multiple design docs. No ambiguity there; the rule above only matters for the four exact
name matches.

## Current state — don't infer it, read it

- [`HANDOFF.md`](HANDOFF.md) — current, dated status. Read this first for "what's actually
  shipped," not what was originally planned.
- [`docs/PLAN.md`](docs/PLAN.md) — the single ordered work queue. Before proposing new work,
  check whether it's already tracked here.
- [`docs/self-reflection-report.md`](docs/self-reflection-report.md) and
  [`docs/critique.md`](docs/critique.md) — two point-in-time self-audits (vision-vs-reality,
  and design-claims-vs-spike-evidence) with every finding marked Resolved and applied, not just
  proposed. Real precedent, not proof of current accuracy: "Resolved" is this project's own
  status claim as of when it was written, not a standing guarantee. If you're touching a module
  one of these docs discusses, spot-check its "Resolved" claim against current source before
  relying on it, the same way you'd verify any other doc claim — treat it as a strong prior, not
  an exemption from verification.
- `specs/archive/spikes/` — real, hardware-verified spike results backing every empirical claim
  in `README.md`/`docs/*.md` (e.g. the code-mode token-savings number). Kept, not deleted, as
  the reasoning trail; `specs/archive/spikes/scripts/` is deliberately excluded from
  `ruff`/`mypy` since it's archival, not maintained production code.

## Source layout

```
src/fabrica/
  civitas_bridge/   # GovernedRuntime/Civitas wiring, NullPresidiumClient
  managers/         # Manager base classes, self-healing pool (GenServer-supervised)
  mcp/              # MCPToolNamespace (client) + FabricaMCPServer (server), both directions
  memory/           # MemoryStore: working memory, compaction, long-term recall (3 facets)
  presidium/        # RestPresidiumClient / InProcessPresidiumClient
  prompts/          # PromptStore, PromptManager
  retriever/        # Retriever engine + find() — shared by tools and skills
  sandbox/          # Sandbox protocol: SubprocessSandbox/SrtSandbox/FirecrackerSandbox, platform-dispatched
  tools/            # ToolNamespace, code-mode execution
  tunnel/           # TunnelProvider (Tailscale Funnel, Cloudflare quick tunnels)
```

## Dev commands

```bash
uv sync --extra dev
uv run pre-commit install                       # ruff/ruff-format/gitleaks on every commit
uv run pre-commit install --hook-type pre-push   # mypy --strict + full test suite on push

uv run ruff check src/ tests/
uv run mypy --strict src/
uv run pytest tests/
```

Hardware-gated tests (Firecracker microVM, `srt` sandboxing) skip automatically on a host
without the required hardware/binary — no manual skipping needed. Full detail:
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Hard rules

- Real, not mocked, verification for anything with a genuine hardware/network/external-service
  dependency — this project's established discipline (see `specs/archive/spikes/` for why).
- Never import `civitas-contrib`, `presidium`, or `civitas` internals directly from core
  `fabrica` modules without going through the documented `PresidiumClient`/`civitas_bridge`
  seams — `presidium` itself is an optional extra (`fabrica-context[presidium]` /
  `fabrica-context[presidium-inprocess]`), not a hard dependency.
- Don't add a new `docs/X.md` design doc without checking whether `docs/contracts/X.md`
  should exist too (or already does) — see the precedence rule above.
