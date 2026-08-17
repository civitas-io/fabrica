# Self-Reflection Report: Vision vs. Reality

**Status:** Point-in-time audit · **Date:** 2026-08
**Method:** Re-read the founding docs (`personas.md`, `problem-definition.md`,
`context-layer.md`, `architecture.md`) cold, restated the actual thesis and
success metrics from them, then checked the real code and docs in this repo
against that restatement — not against memory of what we intended.

---

## 1. The thesis, restated (so the rest of this doc has something to measure against)

**Fabrica is the third pillar of Civitas**: Civitas keeps agents alive,
Presidium keeps them accountable, Fabrica decides *what they see and how they
act on it*. Five owned concerns: tool access (code-mode headline + `find`
fallback), skills (`SKILL.md`-conformant), memory, prompts, isolation
(tiered `Sandbox`, platform-dispatched and hidden from users).

**Five personas, ranked by "blocks everyone else if unsolved"**: Priya
(zero-infra adoption) → Marcus (provable, tiered production isolation) →
Elena (audit/governance seams) → Devon (`SKILL.md` packaging) → Alicia
(vendor-neutral positioning, an aggregator, not new scope).

**Two platform-wide engineering principles, stated explicitly**:
(a) library-first / low-coupling — every component must work standalone;
(b) **Rust for what Fabrica builds, Python only for the interface** — named
directly in `context-layer.md`, with `KeywordBackend` called out as the
first likely candidate.

**The distribution shape**: a two-package split — `fabrica` (protocols +
lightweight defaults, depends only on `civitas`) and `fabrica-contrib`
(opt-in adapters: sandbox backends, retrieval backends, memory backends,
MCP tool source) — explicitly so `pip install fabrica` stays zero-infra and
production upgrades are additive installs, not code changes.

---

## 2. Where we're faithful to the vision — real strengths, not just claims

- **Priya's P1 slice is genuinely solid.** `Retriever`, code-mode execution,
  `SkillManager` against a real 81-skill corpus, `MemoryManager`'s three
  facets, `PromptManager` — all six object-model contracts are real, tested
  code, not stubs. 195 local tests, stable.
- **Marcus's differentiator is real, not aspirational.** `FirecrackerSandbox`
  (Tier 2) is validated end to end on actual hardware — a real tool call
  crossing a real VM boundary over `vsock` — and is reachable through real
  platform dispatch (`select_sandbox_backend()`), not a hardcoded stub. A
  reusable deployment script exists (`scripts/build_firecracker_rootfs.sh`)
  so a second person can actually produce a working image.
- **Devon's `SKILL.md` conformance is real**, validated against the actual
  81-skill `bigpowers` catalog, not a synthetic test fixture.
- **The library-first principle held up under real pressure** — `ToolManager`/
  `SkillManager` staying separate, `MemoryManager`'s three facets staying
  decoupled, `CivitasBridge` as the one named, deliberate exception — all
  traceable, reasoned decisions, not accidents.
- **The "fail closed by default" rule is real and consistently applied** —
  five confirmed instances (`allow_ungoverned`, `allow_unsandboxed`,
  `allow_weak_isolation_for_external_callers`, the `UnsupportedSandboxConfigurationError`
  case, and the `CallbackBridge` no-bypass token), not just stated once.

---

## 3. Real deviations found — the point of this exercise

### 3.1 Critical: the two-package split was never built

`context-layer.md`'s entire distribution story is `fabrica` (core, depends
only on `civitas`) vs. `fabrica-contrib` (opt-in: `[firecracker]`, `[srt]`,
`[libkrun]`, `[mcp]`, etc.). **This does not exist.** There is one package,
`fabrica-context`, and:

- `mcp>=2.0` and `uvicorn>=0.31` are **unconditional, required** dependencies
  in `pyproject.toml` — not gated behind an opt-in extra, even though the
  original design explicitly lists `[mcp]` as a `fabrica-contrib` adapter.
- `FirecrackerSandbox` lives directly in `src/fabrica/sandbox/`, inside the
  core package — not in a separate opt-in module, even though every other
  sandbox tier was explicitly designed as a `fabrica-contrib[...]` extra.
- `civitas` itself is a hard dependency too — but that one **is** an
  explicitly licensed exception (`architecture.md §1a`, `CivitasBridge`).
  `mcp`/`uvicorn` have no equivalent documented exception; they were simply
  never split out.

**Consequence**: anyone who runs `pip install fabrica-context` today gets
`mcp` and `uvicorn` whether or not they ever touch `FabricaMCPServer` or
`MCPClient`. This directly cuts against Priya's stated success metric
("zero required infra for hello-world") in spirit, if not in the letter (it
talks about infrastructure, not dependency footprint — but the package-
structure section this violates is unambiguous about *why* the split
exists).

### 3.2 Significant: a stated engineering principle was reversed, and the docs never caught up

`context-layer.md` and `docs/retrieval.md` both state, in the present tense,
that `KeywordBackend` is a **Rust crate + PyO3 binding** — `retrieval.md`
even calls out that this is a deliberate correction *away from* the
`rank_bm25`-in-Python sketch in RFC 0001.

**The actual code ships exactly the `rank_bm25`-in-Python approach those
docs say was rejected.** The reversal was a reasonable, explicitly-reasoned
call (no performance evidence justified the Rust/PyO3 tooling cost yet,
"ship the default, revisit if forced") — but it was recorded in exactly one
sentence, in `HANDOFF.md`'s narrative log, and **the two documents that
actually assert the opposite were never corrected.** A reader following
`context-layer.md` → `retrieval.md` today would believe something false
about what ships.

### 3.3 Significant: Elena's whole persona is ~unaddressed at the implementation level

This is the largest gap found. `problem-definition.md`'s Elena section sets
a specific, falsifiable bar: *"100% of tool calls, sandbox runs, memory ops,
skill invocations produce a structured audit + usage event."*
`system-design.md §7` names **nine** specific spans.

**Real count in code: one call site** (`execute_in_sandbox.py`'s
`_emit_span`), covering two of the nine (`fabrica.tool.code_mode.run` /
`fabrica.skill.run`) — and even that is a `logger.info` stand-in, explicitly
commented as "not a real OTEL exporter." Missing entirely: `fabrica.tool.find`,
`fabrica.skill.find`, `fabrica.sandbox.acquire`, `fabrica.sandbox.run`,
`fabrica.retriever.search`, `fabrica.memory.write`/`search`,
`fabrica.presidium.check_grant`. `check_grant` itself IS called correctly
before every code-mode/skill run (the actual governance gate works) — but
nothing downstream of that decision is observable the way the design
promises.

Related, same persona: **no credential-injection mechanism into `Sandbox`
exists at all.** Marcus's "zero credential leakage" metric and Elena's
"credential injection into the sandbox" touchpoint both assume a mechanism
that was never designed past a name in `HANDOFF.md`'s deferred-items tail.
Not unsafe today (nothing is injected, so nothing leaks) — but the *feature*
Elena and Marcus were promised doesn't exist.

Also related: the **usage/budget metering half of `civitas-presidium-integration.md`**
(spans carrying `cpu_seconds`/`tool_call_count` etc. tagged with `Scope` so
Presidium can attribute consumption to a budget) has zero real
implementation — `Scope.team_id` exists as a field, but nothing populates or
emits it anywhere.

### 3.4 Moderate: Marcus's isolation ladder has one rung built, not two

The stated success metric is "Tier 0 → Tier 1 (gVisor) → Tier 2 (Firecracker)
is a config change only." **Tier 1 (`gVisor`/`srt` on Linux) does not exist.**
Real dispatch today has exactly two outcomes (Tier 0, Tier 2) — a real,
working ladder, but a shorter one than promised, and it's the middle rung
that's missing, not just the top one.

Also connected: "self-healing pool — a crashed sandbox-pool process
restarts automatically under Civitas supervision" is not true today. No
manager, including `SandboxPool`'s owner, is spawned as a supervised
`GenServer` in v1 — a real, well-reasoned gap (`civitas-bridge.md`'s own
correction: Civitas's dynamic-spawn reconstructs classes from a dotted path
with only `name`, structurally incompatible with DI-constructed managers),
but it means this specific Marcus-facing metric is currently false, not
just unmeasured.

### 3.5 Minor: `context-layer.md`'s scope list never grew to name what MCP client/server actually are

`FabricaMCPServer` and `MCPClient` are both real, well-reasoned additions —
`mcp-server.md` explicitly re-checked the "not Fabrica's concern: generic
MCP proxy/registry" boundary from `context-layer.md` and correctly
distinguished "Fabrica exposing its own capabilities" from "a multi-tenant
proxy," landing on the right side of the line. This is *not* a boundary
violation. But `context-layer.md`'s own "Scope: what Fabrica owns" table —
the five-item list this whole report opens with — was never updated to
list "MCP tool consumption" / "MCP server surface" as owned concerns. A
reader of just that table would not know these exist.

### 3.6 Cosmetic but real: `README.md` is badly stale

It currently says "ten spikes" (actually twelve), "five of six object-model
contracts... 99 tests" (actually all six, 195+ local tests plus 14
real-hardware-only tests), and "Only `CivitasBridge` remains, blocked" —
`CivitasBridge` has been complete for a long time. `FirecrackerSandbox`,
platform dispatch, and both MCP directions aren't mentioned at all. This is
the single most public-facing document in the repo and it describes a
version of Fabrica from several milestones ago.

---

## 4. What's incomplete but is *not* a deviation — already correctly scoped

Distinguishing this matters: these are named, reasoned, tracked gaps, not
things that drifted unnoticed.

- Snapshot/restore, a minimal purpose-built rootfs, `jailer` hardening, real
  per-VM CPU accounting, managed-provider adapters, `TunnelProvider` backends
  — all explicitly deferred in the last two sessions, for stated reasons.
- `PresidiumClient`'s real REST client — genuinely blocked on an external
  dependency (no real Presidium server exists to build against).
- Third-party skill trust/signing, log tamper-evidence — both named
  "deferred, not dropped" in `problem-definition.md` itself, on the same
  footing as each other, since the Define phase.
- Other memory backends beyond Mem0, `SKILL.md` optional fields/bundled
  resources — zero real-corpus evidence either is needed yet.
- Windows Tier 1, macOS/Windows Tier 2 `vsock` equivalents — small segments,
  "spike only if a gap forces it."

---

## 5. Recommended priority, if picking this up

1. **Fix the two documentation-vs-reality contradictions first** — cheap,
   high-value, and currently actively misleading: correct `retrieval.md`/
   `context-layer.md`'s Rust/PyO3 claim to match the real pure-Python
   decision (§3.2), and rewrite `README.md` to reflect actual current state
   (§3.6). Neither requires a product decision, just honesty in the docs.
2. **Decide, deliberately, whether the `fabrica`/`fabrica-contrib` split
   still matters** (§3.1) — either actually split the package (real work:
   move `mcp`/`uvicorn`/`FirecrackerSandbox` behind extras) or consciously
   revise `context-layer.md` to say the split was abandoned and why. Right
   now it's neither done nor undone-on-purpose — that's the actual problem,
   not which answer is correct.
3. **Elena's persona (§3.3) is the biggest real gap and the most aligned
   with "differentiator, not nice-to-have"** — a real OTEL exporter (even a
   minimal one) across the nine named spans would close most of this at
   once, since the span *table* is already fully designed; nothing there
   needs re-deciding, only building.
4. Tier 1 isolation (§3.4) is the natural next isolation milestone if
   `FabricaMCPServer(kind="http")` sees real external traffic — not urgent
   otherwise, since Tier 2 already exists for anyone who needs the bar
   Tier 1 would provide.
5. Everything in §4 stays exactly where it is — correctly deferred, no
   action needed from this report.

**This priority order, plus the full remaining backlog from the previous
"what's remaining" evaluation, is now tracked as one ordered queue in
[`PLAN.md`](PLAN.md)** — easiest first, most complex last, agreed with the
user directly. That doc is authoritative on sequencing; this one stays a
point-in-time record of the findings themselves.
