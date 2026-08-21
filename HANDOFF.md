# Handoff: Fabrica

**Purpose of this doc:** resume work cold, after a context compaction, without
re-deriving anything already decided. Read this first, then follow the links —
don't re-read the whole repo linearly.

**Resume here: [`docs/PLAN.md`](docs/PLAN.md) is the single ordered work
queue** — everything from a self-reflection audit
([`docs/self-reflection-report.md`](docs/self-reflection-report.md), a
point-in-time check of the real code/docs against the founding vision) plus
the full remaining backlog, sorted easiest first, most complex last.

**Status as of this update: Phase 1, Phase 2's Easy tier, and Phase 2's
Medium tier are ALL fully complete.** Phase 2's Complex tier: items 19
(Tier 1 via `SrtSandbox`), 20/20a (snapshot/restore, spiked AND
implemented), 21 (`jailer` integration, spiked AND implemented), 22
(managers-as-GenServers, resolved as a documented finding) are done.
Item 25 (context-footprint metering, added mid-session) is done.

**Item 21 (`jailer` integration) is now DONE, real, shipped, opt-in
(`use_jailer=True`), cold-boot only.** A genuinely hard problem (vsock
inside a directory `jailer` locks down to `700 fc-jail`) was found,
researched against Firecracker's own official docs, and solved --
validated end to end on real hardware, a real guest booting through the
real jail boundary. A second problem (the API socket) was resolved via
Firecracker's own `--config-file` mechanism, schema confirmed directly
against Firecracker's own source. Real infrastructure (a dedicated
`fc-jail` user, FOUR scoped sudoers rules, a real idempotent bootstrap
script) is set up and validated on the homelab. Implemented in
`src/fabrica/sandbox/firecracker_backend.py`, 10 new tests (5 hardware-
gated, 5 pure), verified 3x stable on real hardware with zero leaked
processes or files. Full research trail, every empirical finding, and
the real mechanism: [`specs/archive/spikes/SPIKE-
firecracker-jailer-vsock-integration.md`](specs/archive/spikes/SPIKE-firecracker-jailer-vsock-integration.md)
and `docs/contracts/sandbox.md`'s own "real Tier 2 implementation
notes" section.

**Item 24 (`TunnelProvider` concrete backends) is now DONE for
priorities 1 and 2** -- `TailscaleTunnelProvider` and
`CloudflareTunnelProvider`, both real, shipped, credential-free, and
validated end to end on real hardware (real public URLs, real curl
round trips, real access logs proving requests crossed the tunnel). New
package: `src/fabrica/tunnel/`. `NgrokTunnelProvider` (priority 3)
remains unimplemented, per this contract's own already-stated adoption-
friction note (needs an account for anything beyond a very short
session) -- not blocking. Full mechanism and four real findings from
implementing `CloudflareTunnelProvider` (a genuinely flaky free service,
handled with a real bounded-retry-with-a-fresh-subdomain fix, not just a
longer timeout):
[`docs/contracts/managed-sandbox.md`](docs/contracts/managed-sandbox.md)'s
own "real, shipped implementation" section.

**Only item 23 remains open**: managed-provider adapters (E2B/Modal/
AWS/Azure/GCP) -- genuinely blocked on real paid-tier credentials this
project does not have. See `docs/PLAN.md` directly for the exact state.

---

## What Fabrica is, in one paragraph

Fabrica is the **context layer** of the Civitas platform — the third pillar
alongside Civitas (runtime, keeps agents alive) and Presidium (control,
governance). Fabrica decides what a Python agent sees and where its work
actually runs: tools, skills, memory, prompts, and the sandboxed execution
substrate underneath all of them. Its headline mechanism is **code-mode**: a
model writes code against a tool namespace, that code runs in an isolated
sandbox, and only the final result crosses back into the model's context —
validated by spike to be both ~79% cheaper *and* more correct than traditional
direct tool-calling (`SPIKE-code-mode-execution.md`).

Repo: `civitas-io/fabrica` (public). Design/validate/critique/architecture/
system-design/contracts is complete, and **all six object-model contracts,
both MCP directions, a real self-hosted Tier 2 sandbox (`FirecrackerSandbox`),
real platform dispatch, and real end-to-end OTEL observability are all real,
tested code** (`src/fabrica/`) — see "Current state" immediately below for
exactly what exists and what's still genuinely left (Phase 2's Medium/Complex
backlog in `docs/PLAN.md` — nothing blocking, nothing MDP-critical).

---

## Current state — read this section, not the chronological log below, for "where are we"

**This section is authoritative — rewritten in full again for this
compaction, not appended to, matching this doc's own established
convention.** Detailed narrative for any specific decision (bugs found,
exact reasoning) lives in `git log` commit messages and the relevant
`docs/contracts/*.md`'s own "Real addition"/"Correction found during
implementation" sections — not repeated here. This section states facts
and points at where the reasoning actually lives, not a retelling.

### Design phase: complete

Full discovery→define→design→validate→critique→architecture→system-design→contracts
arc. Fifteen spikes, all real hardware/API evidence, in `specs/archive/spikes/`
(twelve from the original arc, plus
[SPIKE-tessera-credential-integration.md](specs/archive/spikes/SPIKE-tessera-credential-integration.md)).
Nine contracts (`Retriever`, `Sandbox`, `managers.md`, `memory.md`,
`prompts.md`, `civitas-bridge.md`, `mcp-integration.md`, `mcp-server.md`,
`managed-sandbox.md`). Platform-wide rules confirmed multiple times, safe
to apply without re-deriving: **library-first/low-coupling**
(`architecture.md §1a`); **requests, never reaches in** (toward Civitas
and Presidium alike); **external dependencies are always fully-constructed
objects, never raw config**; **fail closed by default, explicit greppable
opt-in to bypass** (five confirmed instances: `allow_ungoverned`,
`allow_unsandboxed`, `allow_weak_isolation_for_external_callers`,
`UnsupportedSandboxConfigurationError`, `CallbackBridge`'s no-bypass
token).

### Implementation phase: real, tested, and substantially beyond the original object model

`src/fabrica/` — 232 tests total (217 passing locally + 15 that only run
for real on Linux+KVM+Firecracker, skipped elsewhere, verified 15/15 on
the homelab), clean `ruff`/`mypy --strict`, stable across repeated runs
(process-exit cleanliness independently verified, not just assertion
pass/fail — see the sandbox-timeout entry below for why that distinction
matters).

**All six object-model contracts, both MCP directions, self-hosted Tier 2
isolation, real platform dispatch, and real end-to-end observability are
real code, not designs waiting on implementation:**

- **`Retriever`** (`src/fabrica/retriever/`) — `KeywordBackend`, pure-Python
  BM25 via `rank-bm25`. **Known, deliberate doc/code gap, now corrected**:
  `context-layer.md`/`retrieval.md` originally claimed this ships as a
  Rust+PyO3 binding; the real, shipped v1 is pure-Python — no performance
  evidence yet justifies the Rust tooling cost. Both docs corrected to
  say so plainly, including in the rendered `package-structure.svg`
  diagram itself.
- **`Sandbox`/`SandboxPool`** (`src/fabrica/sandbox/`) — **two real
  backends**, not one: `SubprocessSandbox` (Tier 0) and
  `FirecrackerSandbox` (Tier 2, self-hosted, validated end to end on real
  hardware including a real `vsock` tool call crossing an actual microVM
  boundary — see `contracts/sandbox.md`'s own "real Tier 2 implementation
  notes"). **Real, automatic platform dispatch**
  (`fabrica.sandbox.select_sandbox_backend()`) picks between them —
  `CivitasBridge.build()` calls it by default; the exact two real outcomes
  today (Tier 1/`gVisor`/`srt` and macOS/Windows Tier 2 remain
  unimplemented) are stated honestly, not oversold. A reusable script
  (`scripts/build_firecracker_rootfs.sh`) builds the deployable rootfs
  image, documented in `docs/deployment/firecracker-rootfs.md`.
  `SandboxPool.close()` is real (a genuine shutdown gap found by testing
  against a real backend, not the fast in-memory test double). Both
  `acquire()`/`run()` accept an optional `tool_call_timeout`, closing
  `contracts/sandbox.md`'s own open item 3 — finding and fixing, along
  the way, a real hang in `SubprocessSandbox`'s cleanup path only visible
  by checking process-exit cleanliness, not test assertions (full story
  in `contracts/sandbox.md`'s open-items resolution, not repeated here).
- **`managers.md`** (`src/fabrica/managers/`, `src/fabrica/tools/`,
  `src/fabrica/presidium.py`, `src/fabrica/scope.py`) — `execute_in_sandbox`,
  `ToolManager`, `SkillManager`. `SkillManager`'s `SKILL.md` parser
  validated against the real 81-skill `bigpowers` catalog.
  `PresidiumClient` here is the Protocol only — the real REST+mTLS
  implementation is genuinely blocked externally (see "What's left"
  below).
- **`MemoryManager`** (`src/fabrica/memory/`) — all three facets
  (`InMemoryWorkingMemoryStore`, `RecencyCompactor`/`NullCompactor`,
  `InMemoryMemoryStore`), plus `PersistedMemoryStore` for `CivitasBridge`
  service mode (a real `ComponentStateHandle`-backed adapter, not the
  in-memory default).
- **`PromptManager`** (`src/fabrica/prompts/`) — `InMemoryPromptStore`
  with real atomic version assignment under concurrency, plus
  `PersistedPromptStore` for service mode. `PromptTemplate` now validates
  its own `content` size (256KB ceiling) and `cache_boundary` range at
  construction time — real, rejecting errors
  (`PromptTooLargeError`/`InvalidCacheBoundaryError`), not silent
  pass-through or truncation.
- **`CivitasBridge`** (`src/fabrica/civitas_bridge/`) — all six
  object-model contracts compose here. `civitas>=0.11.0` is a real
  runtime dependency (the one deliberate exception to "depend on shapes,
  not packages," per `architecture.md §1a`). `request_supervision`/
  `request_state_persistence` are both real and tested against genuine
  `civitas.runtime.Runtime`/`DynamicSupervisor`/`InMemoryStateStore` — not
  hand-rolled test doubles. `build()` is idempotent (a second call
  returns the same `Fabrica` instance — a real decision, not an
  oversight); `dynamic_supervisor_name` is validated upfront via
  `civitas.runtime.Runtime.get_agent()`, raising a clear
  `SupervisorNotFoundError` instead of a later bus-routing failure. A
  `tracer` constructor parameter wires a real `civitas.observability
  .tracer.Tracer` through the whole object graph (see the observability
  entry below).
- **`MCPClient`/`MCPToolNamespace`** (`src/fabrica/mcp/`) — a real MCP
  client against the actual, current `mcp` v2.0.0 SDK, `srt`-sandboxed
  connections included. **Validated against a second, real, independently-
  built system**: `tests`/the credentials spike prove this composes with
  Tessera's real `tsr mcp` server with zero adapter code (see
  `docs/credentials.md`).
- **`FabricaMCPServer`** (`src/fabrica/mcp/server.py`) — both stdio AND
  HTTP transports real (HTTP reuses `mcp`'s own bearer-auth middleware,
  not hand-rolled ASGI). `WeakIsolationError`'s tier check is real and
  now has a genuine Tier-2 answer to give it (`FirecrackerSandbox` via
  platform dispatch), not just an always-must-opt-out Tier-0-only world.
- **Real, end-to-end observability** (`src/fabrica/observability.py`) —
  all ten spans named in `system-design.md §7` are real (originally nine
  -- `PromptManager` gained its own `fabrica.prompt.get`/`fabrica.prompt
  .put` pair afterward, closing a gap where it emitted nothing at all,
  not just a missing attribute), not the single
  `logger.info` stand-in this project shipped with earlier. `Tracer`/
  `Span` are structural Protocols matching `civitas.observability.tracer
  .Tracer`'s real, public shape exactly — a real, load-bearing finding:
  Civitas does NOT use OpenTelemetry's global `TracerProvider` registry,
  so a generic `opentelemetry.trace.get_tracer()` call would not actually
  have routed through Civitas's own span pipeline. Every component
  defaults to `NullTracer()` (real no-op); `CivitasBridge`'s `tracer`
  parameter wires in a real one. Full nested trace trees (code-mode's
  outer span genuinely parents `check_grant`/`sandbox.acquire`/
  `sandbox.run`), not disconnected same-prefix spans. Real usage/budget
  consumption events ride these same spans (`latency_ms`, `volume_bytes`)
  — no separate metering subsystem exists or was needed.
- **Credentials: a real, validated architectural decision, not a
  subsystem** (`docs/credentials.md`) — `Sandbox` gets NO
  credential-injection mechanism at all, deliberately. Tessera (a real,
  separately-built agent-blind credential broker) independently arrived
  at the same rule Fabrica needed (never inject a secret into an
  interpreter running untrusted code) — validated end to end with zero
  new Fabrica code: a real `tsr mcp` process composed with Fabrica's real
  `MCPClient` inside a real `SubprocessSandbox` code-mode run.

**Not built yet, deliberately, and named as such, not hidden**: real
`fabrica-contrib[mem0|zep|letta|cognee|langmem]` memory adapters (need
real external services to validate against); the `fabrica`/
`fabrica-contrib` package split itself (a real, decided deferral until
closer to a release — see `context-layer.md`'s own "Decided, not just
found stale" note); Tier 1 isolation (`gVisor`/`srt`); managed-sandbox
provider adapters and `TunnelProvider` backends (interfaces designed,
credentials/priority both block real implementation).

### What's left, in priority order

**The full, current, line-by-line list is [`docs/PLAN.md`](docs/PLAN.md)
— read that, not this summary, before picking up work.** High-level
status:

1. **Phase 1 (the six self-reflection fixes) — DONE, in full.**
2. **Phase 2's Easy tier (items 7–12) — DONE, in full.**
3. **Phase 2's Medium tier (items 13–18) — DONE, in full.** `Retriever`'s
   eager-cache invalidation/batch atomicity; `RecencyCompactor`'s and
   `PromptManager`'s two open items (both found already resolved in
   code, just needed the docs to say so); `FirecrackerSandbox` real
   per-VM CPU accounting (found Firecracker's own `/metrics` API is
   write-only, checked against the real OpenAPI spec -- real fix reads
   `/proc/<pid>/stat` instead); a real, minimal, purpose-built
   Firecracker rootfs (Ubuntu 24.04 + `python3` only, ~3.5x faster
   per-instance rootfs copy, verified on the homelab).
4. **Phase 2's Complex tier (items 19–24) — item 19 DONE** (Tier 1
   isolation via `SrtSandbox`, built directly by the user in a parallel
   session, reviewed afterward -- one real resource leak found and
   fixed, `Sandbox` gained a `close()` lifecycle method as a result).
   **Item 22 walked through directly with the user (Civitas's own
   maintainer) and resolved as a documented finding, not built** --
   managers holding live constructor-injected dependencies can't
   survive Civitas's real dynamic-spawn mechanism, AND, more
   fundamentally, none of Fabrica's managers hold state that would
   justify the bigger capability the maintainer actually described
   wanting (real cross-node process migration, not just restart-from-
   config) -- full reasoning in `system-design.md`'s own "Finding:
   managers as supervised GenServers, investigated but not built"
   section, with concrete, named revisit triggers, not a permanent
   rejection. **Items 20, 21, 23, 24 remain open**: `FirecrackerSandbox`
   snapshot/restore, `jailer` integration, managed-provider adapters,
   `TunnelProvider` backends.
5. **Added mid-session, not part of the original backlog (item 25) --
   DONE.** Context-footprint metering extended from `MemoryManager` to
   `ToolManager.find()`/`SkillManager.find()`/`PromptManager` -- a
   direct user question ("should we consider other costing parameters
   to track?") walked through and scoped: Fabrica captures and emits
   real numbers, billing/policy stays entirely Presidium's job.
6. **BLOCKED, not just deferred: `PresidiumClient`'s real REST+mTLS
   implementation.** No real Presidium HTTP server exists anywhere to
   build/validate a client against. Revisit only if a real Presidium
   deployment ever exists to build against; not actionable from this
   repo alone.
7. **Deliberately deferred, named, not re-litigated per `PLAN.md`'s own
   closing section**: third-party skill trust/signing, log
   tamper-evidence, other memory backends beyond Mem0, `SKILL.md`
   optional fields/bundled resources, Windows Tier 1, macOS/Windows
   Tier 2 `vsock` equivalents.

**Immediate next action**: open `docs/PLAN.md`, pick up the Complex
tier at item 20 (`FirecrackerSandbox` snapshot/restore -- needs its own
spike before implementation, a genuinely unvalidated combination with
the already-live vsock bridge) or item 23/24 (managed-provider
adapters/`TunnelProvider` backends -- real implementation work against
already-designed interfaces). Nothing above it is blocking; item 6
(`PresidiumClient`) is the only genuinely external blocker and does not
gate anything else.


## Read in this order if you're new to this

1. `README.md` — entry point, links to everything.
2. `docs/architecture.md` — visual walkthrough (8 SVG diagrams), the external/product view.
3. `docs/system-design.md` — internals: object model, deployment topology, error
   handling, state ownership (3 more SVG diagrams). One level below architecture.
4. `docs/critique.md` — every design claim checked against evidence. Read this to
   know what's *actually proven* vs. what's still an assumption.
5. `docs/contracts/*.md` — implementation-ready signatures, growing one component
   at a time (`Retriever`, `Sandbox`, then the managers).

---

## The full arc of work, in sequence (for context on *how* decisions were made)

1. **Discovery** — `docs/personas.md`: 5 personas (Priya/Marcus/Elena/Devon/Alicia)
   + JTBD, plus the model itself as a non-human actor.
2. **Define** — `docs/problem-definition.md`: per-persona problem statements,
   success metrics, non-goals, in build-priority order.
3. **Design** — `docs/context-layer.md`, `tool-execution.md`, `isolation.md`,
   `retrieval.md`, `memory.md`, `skills-gateway.md`,
   `civitas-presidium-integration.md`, `landscape.md`.
4. **Validate** — 9 spikes in `specs/archive/spikes/` (10th added later, see below),
   all real hardware/API
   evidence, not simulated:
   - `SPIKE-tool-retrieval-token-overhead.md` — `find_tools` stays near-flat vs. static linear growth (real Claude-on-Vertex calls)
   - `SPIKE-tool-disambiguation-retrieval-quality.md` — embeddings beat keyword matching (100% vs 67% precision@3); **includes the prx detour** that found prx's off-the-shelf model hits 100% precision@3 on this task
   - `SPIKE-prx-invocation-latency.md` — persistent process beats subprocess-per-call (37ms vs 75ms); found the cold/warm start gap (~260ms vs ~5ms)
   - `SPIKE-skill-progressive-disclosure.md` — validated against the **real bigpowers SKILL.md catalog** (not synthetic data); found the original skill-index design wasn't actually O(1)
   - `SPIKE-firecracker-boot-restore-latency.md` — real bare-metal hardware (homelab, SSH); restore 8.1–10.7ms confirmed; found boot-time claims conflated two different signals (VMM-ready 10.5ms vs. userspace-ready 1,055ms)
   - `SPIKE-macos-isolation-srt-libkrun.md` — `srt` real numbers (p50 152ms); libkrun confirmed to have **no snapshot/restore at all** (permanent ceiling)
   - `SPIKE-code-mode-execution.md` — **the most important one.** Validated the actual headline mechanism (not just the fallback): ~79% cheaper AND exactly correct in 3/3 runs, while the traditional approach was wrong in 3/3 runs
   - `SPIKE-memory-mem0-wrap.md` — real Mem0 integration; found it requires an API key by default (contradicts zero-infra assumption) and has an internal `add()`/`search()` parameter inconsistency
   - `SPIKE-zmq-sandbox-channel-feasibility.md` — confirmed `pyzmq` is viable (1.6MB, self-contained, 0.73ms round trip) for the Tier 0/1 sandbox callback
   - **10th spike, added post-contracts**: `SPIKE-recency-compactor-validation.md` —
     closed the project's biggest evidence gap (`RecencyCompactor` had zero
     validation until now). Real Gemini calls, 5 runs each: full history 5/5
     grounded-correct, `RecencyCompactor`'s real strategy 5/5 (matched the
     baseline exactly), naive truncation 0/5 genuinely grounded (it landed on
     the same answer every time but never once via real reasoning from the
     actual constraint). Caught and fixed a real false-positive in its own
     checker mid-spike — documented, not hidden. Validates the core
     summarize-vs-truncate mechanism; `preserve_last_n=6` itself remains an
     unvalidated guess, precisely scoped as still open.
5. **Critique** — `docs/critique.md`: every claim checked, 11 corrections + 5 open
   architecture decisions, **all resolved and applied**, not left as proposals.
6. **Architecture** — `docs/architecture.md` + `docs/assets/*.svg` (8 diagrams).
   **Hand-crafted SVG only, no Mermaid** — matches the visual system already used
   in `python-civitas`/`presidium`'s `docs/assets/` (dark cards, blue=Runtime,
   purple=Control, **amber=Context**, the new addition for Fabrica).
7. **System design** — `docs/system-design.md` + 3 more SVGs. Internal object
   model, library-vs-service topology, the internal code-mode sequence
   (including the sandbox→`ToolManager` callback loop). **5 open questions, all
   resolved** through direct back-and-forth (see below) — none left as
   placeholders.
8. **Contracts** (in progress) — `docs/contracts/`:
   - `retriever.md` — done. Found real gaps writing it (no removal method, no
     way to enumerate eager items, `kind` parameter question resolved).
   - `sandbox.md` — done. **Found and corrected a real bug**, not just a gap:
     `system-design.md`'s original resolution said a used sandbox instance gets
     reused after an overflow — wrong, arbitrary code may have left arbitrary
     state; corrected to always-terminate-and-restore-fresh.
   - `managers.md` — done. Formalized `PresidiumClient.check_grant` (never
     raises on Presidium-unreachable — returns `deny` as a plain value, so a
     broad `except:` can't accidentally treat an outage as permissive),
     `execute_in_sandbox` (the literal implementation of "composition, not
     inheritance"), `ToolManager`, `SkillManager`.
   - `memory.md` — done. Kept `MemoryItem.score` unlike `RankedMatch`
     (no counter-evidence exists for memory recall the way there was for
     tool retrieval — not copying the no-score rule by reflex). Made
     `Message.tokens` required, not optional, specifically to avoid Fabrica
     needing to bundle or guess at a model-specific tokenizer.
   - `prompts.md` + `contracts/prompts.md` — done, and the FIRST manager written
     design-doc-first (the other three formalized existing design docs; this
     one had none until now, and zero spike coverage — stated honestly, not
     hidden). Deliberately excludes both rendering and prompt compression, per
     `architecture.md §1a`'s library-first principle applied from the start:
     rendering is a harness decision.
   - `prompts.md` was then extended with a grounded "Explored: the wider
     feature space" survey (provider-side prompt caching mechanics, DSPy/
     TextGrad automated tuning, LLMLingua-2 extractive compression, registry/
     hub table-stakes features, a `PROMPT.md` portable format precedented by
     both Humanloop and this codebase's own `SkillManager.load()`). This
     **corrected an earlier claim**: compression does NOT belong as a
     `Compactor` extension — LLMLingua-2's extractive token-classification
     approach is mechanically nothing like `Compactor`'s abstractive
     `Summarizer`-based one; it's closer in shape to a `RetrieverBackend`
     (wrap a small local model) than to `Summarizer`. Two ideas flagged as
     worth prioritizing soon, not yet built: a cache-boundary marker
     (low-coupling, high-leverage) and the `PROMPT.md` format.
   - **Both promoted into `contracts/prompts.md`.** Resolved a real tension
     while doing so: `PromptManager` can't detect a template's cache
     boundary itself (it's contractually forbidden from parsing `content`'s
     templating syntax), so `cacheable`/`cache_boundary` are
     author-declared fields, stored and returned verbatim, never validated
     -- the author makes the claim, `PromptManager` just carries it.
     `load(path)` reads a `PROMPT.md` file (frontmatter + body, mirroring
     `SKILL.md`) and is idempotent for unchanged content, same pattern as
     `ToolManager.register()`.
   - **`CivitasBridge` — now done too.** See the `CivitasBridge` design
     walkthrough section further down for the full reasoning trail; all six
     object-model contracts are complete as of this revision.

---

## The five `system-design.md` decisions, resolved through direct challenge (worth knowing *how*, not just *what*)

Each of these went through a real back-and-forth, not a first-draft acceptance —
worth preserving the reasoning, not just the answer, since it shows the pattern
to keep using:

1. **Warm-pool-exhausted:** hybrid bounded overflow (cold-start up to a ceiling,
   then queue) — chosen specifically because unbounded cold-start is the exact
   "runaway resource consumer" shape Marcus's persona names as a fear.
2. **`PresidiumClient` transport:** REST+mTLS (confirmed Presidium is a genuinely
   separate deployment, not co-located). This led to removing `emit_usage_event`
   entirely — usage rides OTEL spans instead (async by construction, reusing
   existing plumbing instead of building new).
3. **Sandbox callback transport:** started as "vsock uniformly?", challenged
   twice — first to confirm the *trusted* `ToolManager`↔`SandboxPool` hop should
   just reuse Civitas's own transport ladder, then to correct an implicit
   Linux-only assumption (macOS has `VZVirtioSocketDevice`, Windows has
   `AF_HYPERV`/Hyper-V Sockets, the same thing WSL2 itself uses) — landed on
   ZMQ-with-a-relay, sanity-checked by spike.
4. **`ToolManager`/`SkillManager` split:** composition over inheritance — kept
   separate (genuinely different trust models: arbitrary generated code vs.
   author-trusted named scripts), shared execution plumbing via one helper
   function, not a base class.
5. **Mode-switching granularity:** phased — v1 ships one flag, but
   `CivitasBridge`'s internals are built as if per-component granularity
   already exists, so v2 (per-component overrides) is additive, not a rework.

---

## THE OPEN QUESTION THAT TRIGGERED THIS HANDOFF — resolved

**Resolved:** Fabrica's target is confirmed as general Civitas agents, not
coding agents specifically — but the resolution came with a real reframe, not
just a confirmation. `docs/memory.md` was rewritten to cover **three facets**,
not one: working memory (session-scoped scratchpad), compaction (a
harness-engineering primitive — Civitas decides *when* to compact, Fabrica
decides *how*), and long-term memory (the original Mem0-style design,
unchanged). The concrete trigger for adding compaction as a first-class facet:
**this very `HANDOFF.md` was a manual, human-triggered instance of exactly that
mechanism** — a person noticed context pressure and asked for a checkpoint by
hand, which is precisely the kind of thing a generic `MemoryManager` should
offer as a callable primitive instead.

Key design decision: `Compactor` never makes its own LLM call (would violate
"wrap, don't build" by requiring Fabrica to hold model credentials) — a
`Summarizer` is injected via real DI at construction time, same pattern as
`Sandbox`/`RetrieverBackend`. See `docs/memory.md`'s new sections for the full
`WorkingMemoryStore`/`Compactor`/`Summarizer`/`MemoryManager` shape.

**Update:** `docs/contracts/memory.md` is now written (see the Contracts list
above). Remaining flagged there, not resolved at the time: `RecencyCompactor`
had zero empirical backing (no spike) — **later closed**, see
`SPIKE-recency-compactor-validation.md` above. A single-message-exceeds-budget
edge case still has no defined behavior.

---

## What's explicitly left open elsewhere (not oversights — named, not buried)

- **Two remaining critique gaps** (`docs/critique.md`, closing section): other
  memory backends beyond Mem0 (Zep/Letta/Cognee/LangMem) untested; `SKILL.md`'s
  optional fields and bundled-resource loading (`scripts/`/`assets/`/`references/`)
  untested — zero bigpowers skills exercise that path.
- **Tier 2 sandbox relay -- RESOLVED for Firecracker/Linux**: the harder
  half (an actual cross-VM-boundary relay, not just architecture) is now
  real and validated on real hardware
  (`SPIKE-firecracker-vsock-callback-bridge.md`, `FirecrackerSandbox`).
  The `VZVirtioSocketDevice` (macOS/libkrun) and `AF_HYPERV` (Windows)
  equivalents remain completely unproven -- genuinely still open, just a
  narrower gap than before (one platform done, two remain, not zero).
- **Contract-level open items still genuinely open** (Phase 2's Medium/
  Complex tiers, `docs/PLAN.md`): `Retriever`'s eager-cache invalidation
  and batch-atomicity; `RecencyCompactor`'s single-message-exceeds-budget
  edge case; `PromptManager`'s cache eviction policy;
  `FirecrackerSandbox`'s real per-VM CPU accounting and a minimal
  purpose-built rootfs image. (`Sandbox`'s replenishment-scheduling
  mechanism, release-on-unknown-handle behavior, `on_tool_call`'s own
  timeout, and `managers.md`'s `find()`-kind-override question, listed
  here in earlier revisions, are now all resolved -- see each contract's
  own open-items list.)
- **Windows Tier 1** (`srt`'s Windows mode) — deliberately deferred, not
  blocking, per explicit direction ("small segment, spike only if a gap forces it").
- **macOS Tier 2** (libkrun) ships despite having no snapshot/restore — explicit
  decision ("great to have, not a must"), not an oversight.

---

## A named platform-wide principle (added after four contracts were already written)

`architecture.md §1a`: **library-first, low coupling / high cohesion** — every
component must work as an independently reusable piece; only the orchestrator
layer (`CivitasBridge`/Civitas) is allowed to be tightly integrated. This was
driving decisions all session without being named (separate `ToolManager`/
`SkillManager`, `prx`/`tessera` staying outside Fabrica, swappable `Protocol`
backends everywhere) until a sidebar about graph-based dynamic memory
compaction (comparing Fabrica's `MemoryManager` to Generative Agents/MemGPT/
MemOS prior art) surfaced it directly: Fabrica's three memory facets
deliberately do NOT share one unified retrieval/retention score, unlike
Generative Agents' single composite formula, specifically because a shared
score would couple `WorkingMemoryStore`/`Compactor`/`MemoryStore` to each
other's internal signals. See `memory.md`'s "Related work and a deliberate
divergence" section for the full reasoning. **Apply this principle explicitly
when writing `PromptManager`/`CivitasBridge`'s contracts** — it's now a named
rule, not something to re-derive.

## Process conventions established this whole session — keep using them

- **Spikes:** lock a specific question + timebox before writing any code
  (`spike-prototype` skill discipline). Code is held in
  `specs/archive/spikes/scripts/`, not deleted, per explicit instruction — findings
  docs are the durable artifact.
- **Diagrams:** hand-crafted SVG only, matching the existing platform palette
  (dark cards `#111827`; blue `#2563EB` Runtime; purple `#7C3AED` Control; amber
  `#D97706` Context). **Always render to PNG and visually inspect before
  finalizing** — this caught multiple real layout bugs (clipped columns,
  overlapping text) that would have shipped silently otherwise.
- **Relative links from `specs/archive/spikes/*.md` back into `docs/` need
  `../../../docs/...`** (three levels up), not `../../docs/...` — this exact
  mistake was made **three separate times** across different spike files
  before being caught by explicit verification. Check this specifically on
  any new file in that directory.
- **git/GitHub:** active `gh` account must be `jerynmathew` (admin on
  `civitas-io`), not `jeryn-fiddler`. The macOS keychain sometimes overrides
  the active-account switch for plain `git push` — if push fails with a
  permission error against the wrong user, use
  `GIT_ASKPASS=<script echoing jerynmathew + token> GIT_TERMINAL_PROMPT=0 git -c credential.helper= push`
  to force the correct credential without it leaking into `.git/config`.
  Always verify `.git/config` has no embedded token after pushing.
- **Every resolution keeps its reasoning trail** — corrections and resolved
  open questions are marked `~~struck through~~` **Resolved** with the
  original text preserved, never deleted. This handoff doc is written in that
  same spirit: don't just say what was decided, say how, so the next session
  can extend the pattern instead of re-deriving it.

---

## `CivitasBridge` design walkthrough — in progress, two decisions settled

Before writing `contracts/civitas-bridge.md`, walked through where `§1a`'s
"only the orchestrator integrates tightly" license actually ends. Two things
settled, both corrections to what `system-design.md` said before:

1. **Construction-time wiring only (v1), a bounded runtime extension possible
   later (deferred, not ruled out).** `CivitasBridge`'s job is assembling the
   object graph once (`build() -> Fabrica`) — never per-turn orchestration
   across managers, which would duplicate Civitas's own runtime loop. Kept
   extensible the same way mode-switching was: `build()` must return every
   manager as a public attribute on `Fabrica`, never hidden exclusively
   behind `CivitasBridge` — that's what would make a later, narrow,
   opt-in, read-only convenience method (never a decision-making one)
   additive rather than a rework.
2. **`CivitasBridge` requests, Civitas performs — corrects a real
   inconsistency, not just adds detail.** `system-design.md` previously said
   `CivitasBridge` "registers GenServers with the supervision tree,"
   implying it reaches into Civitas's internals directly. Wrong, and
   inconsistent with `PresidiumClient`'s own design one paragraph earlier in
   the same doc (`check_grant` asks, Presidium decides). Fixed: `CivitasBridge`
   calls `request_supervision(...)`/`request_state_persistence(...)`; Civitas's
   own runtime decides how to fulfill each request. This also fixed a second,
   previously-unnoticed inconsistency: `PromptManager`/`MemoryManager` were
   described as depending on Civitas's `StateStore` directly, which would have
   quietly created a THIRD place this system talks outward, contradicting
   §1's own claim that there are only two (`CivitasBridge`, `PresidiumClient`).
   Both managers' state persistence is now explicitly mediated through
   `CivitasBridge.request_state_persistence` — fixed in both
   `system-design.md` (component matrix + state table) and `memory.md`
   (Integration section).

**`Summarizer` DI entry point — resolved.** `CivitasBridge.__init__` takes
`summarizer: Summarizer | None = None` — a plain typed keyword parameter, not
a generic dependency-registry bag (that would be premature generalization:
`Summarizer` is the ONLY dependency in this whole system that structurally
cannot have a zero-config default, so there's no real risk yet of
`CivitasBridge`'s constructor bloating with more of these — revisit
generalizing only if a second one actually appears). Optional, not required,
so constructing `Fabrica` never forces every deployment to supply a model
connection just to start up.

This meant `MemoryManager`'s required `Compactor` constructor parameter
needed an answer for `summarizer=None`: **`NullCompactor`**, a Null Object
implementing `Compactor`, wired in by `CivitasBridge` instead of
`RecencyCompactor` when no `Summarizer` was configured. Raises
`CompactionUnavailableError` only when `compact()` is actually invoked —
keeps `MemoryManager`'s constructor simple (always receives a valid
`Compactor`, no `Optional`/`None`-branching at every call site) and answers
"is compaction configured?" once, at construction time, not repeatedly at
every call site. Written into `contracts/memory.md` already, ahead of
`CivitasBridge`'s own contract, since it's `Compactor`/`MemoryManager`'s
behavior being specified, not `CivitasBridge`'s.

**Presidium-absent question — resolved, asymmetrically from `NullCompactor`
on purpose.** `check_grant` is mandatory hot-path control flow (called by
`execute_in_sandbox` on every execution), unlike compaction which a harness
invokes optionally — so `NullPresidiumClient` must ALLOW by default, the
opposite failure direction from `NullCompactor`. To keep that from being a
silent security hole: `presidium_client=None` requires an explicit
`allow_ungoverned=True` or `CivitasBridge` raises `UngovernedConfigurationError`
at construction — forces a visible, greppable decision rather than a default
you fall into by omission.

**DI shape for `presidium_client` — resolved, mirrors `summarizer` exactly.**
A fully-constructed `presidium_client: PresidiumClient | None` object, not a
raw endpoint string — a bare endpoint would have been insufficient anyway
(mTLS needs certs, not just a URL) and would have forced `CivitasBridge`'s
constructor to slowly absorb `PresidiumClient`'s entire configuration surface.
Named as a general rule, not a one-off: **`CivitasBridge` accepts
fully-constructed objects for every external dependency, never raw config it
would translate itself** — applies to any future DI'd dependency by default.

## `CivitasBridge` — DONE. All six object-model contracts complete.

`contracts/civitas-bridge.md` written, incorporating every decision above.

**`CivitasRuntime` reconciliation — done, not left provisional.** Read
`python-civitas`'s actual source directly (`civitas/runtime.py`,
`civitas/genserver.py`, `civitas/plugins/state.py`), not just its docs, and
found the real API differs from the first draft in two concrete ways:

1. There is no "register a supervision spec" call. The real mechanism is
   `Runtime.spawn(supervisor_name, agent_class, name, config, *, wait=True) ->
   str` — dynamically spawning an agent into an **already-existing, named**
   `DynamicSupervisor` that Civitas's own deployment topology defines.
   `CivitasBridge` doesn't create a supervisor or choose a restart strategy;
   it spawns into one Civitas already set up, and needs a new
   `dynamic_supervisor_name` constructor parameter to know which one. Returns
   the spawned agent's name (a string), raises `SpawnError` on failure — not
   an opaque handle.
2. `civitas.plugins.state.StateStore` is far simpler than guessed: keyed by
   `agent_name: str`, storing `dict[str, Any]` directly. Replaced the
   invented byte-oriented `StateHandle` with `ComponentStateHandle` — a thin
   wrapper pre-bound to one component's name over the real `StateStore`, so a
   manager can't accidentally address another component's state.

`CivitasBridge.__init__` now requires `civitas_runtime`, `civitas_state_store`,
AND `dynamic_supervisor_name` together in service mode (`RuntimeRequiredError`
if any is missing) — all three genuinely needed given the real API, not one
combined "runtime" object as first sketched. This is a real correction, not
just filling in the previously-flagged gap with confirmation — the original
guess was wrong in its shape, not just underspecified.

---

## Immediate next action (supersedes any earlier "next action" note above) — HISTORICAL, itself now superseded by "Current state" near the top of this document

*This section's own "supersedes any earlier note" claim is exactly the kind
of drift the new "Current state" section at the top now exists to prevent —
kept here as reasoning-trail detail, not as the place to look for current
status.*

All six object-model contracts are done: `Retriever`, `Sandbox`,
`managers.md` (`PresidiumClient.check_grant`, `execute_in_sandbox`,
`ToolManager`, `SkillManager`), `memory.md`, `prompts.md`, `civitas-bridge.md`.
The design/validate/critique/architecture/system-design/contracts arc that
`README.md`'s reading order describes is now complete end to end.

**A live PyPI naming collision was also found** — the name `fabrica` is
already taken on PyPI by an unrelated third party (a Codex-transport
scaffold, v0.0.7) — neither this repo NOR `civitas-contrib/packages/fabrica`
(a real, separate, code-containing package that also claims `name = "fabrica"`
in its `pyproject.toml`, discovered while investigating this) can ever ship
under that name. Candidate alternates checked available at the time:
`civitas-fabrica`, `fabrica-context`, `pyfabrica`, `fabrica-ctx`,
`civitas-context`, `fabrica-agent`, `fabricapy`.
**Resolved later, after a more thorough second pass (18 of 23 candidates
checked across PyPI + both Homebrew registries): `fabrica-context`** — see
"Current state" at the top of this document for the full reasoning.

**`civitas-contrib`'s three stale Fabrica documents are still unfixed** —
`docs/design/fabrica.md`, `packages/fabrica/README.md`,
`rfcs/0001-tool-retrieval.md` all describe the old, narrower `find_tools`-only
version of Fabrica (framing `find_tools` as the solution, the exact reverse of
this project's actual conclusion that code-mode is the headline and `find()`
is the fallback), with nothing anywhere pointing to `civitas-io/fabrica`.
Fixing this was paused specifically because the naming collision means a
"here's the real repo" pointer can't yet say what to `pip install`.
**`packages/fabrica`'s real code — resolved: migrate, don't archive.**
Designed [mcp-integration.md](docs/mcp-integration.md): the old `MCPClient`/
`BubblewrapSandbox` code (~500 lines) becomes the validated implementation
behind a new `MCPToolNamespace` — one more `ToolNamespace` implementation,
zero changes needed to `ToolManager`/`SandboxPool`/`execute_in_sandbox`.
Explicitly reconciled against `landscape.md §2`'s "do not build an MCP
gateway" decision: this is an internal adapter for Fabrica's own
tool-execution pipeline (inherits Presidium governance for free via the
existing `execute_in_sandbox` grant check), not a standalone gateway product
with its own discovery/aggregation/governance — the thing that's rejected.
Also distinguished from a second, already-noted direction in
`tool-execution.md` (Fabrica *as* an MCP server) — this is the client
direction, deliberately designed separately. The `bwrap` sandboxing stays
internal to the adapter, never routed through `SandboxPool`'s tier system
(different lifecycle shape: persistent connection vs. ephemeral execution).
Only the old package's stale `find_tools`-only *docs* get retired — the code
migrates.

What's left, roughly in order of how blocking each is:

1. ~~Resolve the PyPI naming collision~~ **Resolved: `fabrica-context`** —
   see "Current state" at the top of this document.
2. **Fix the three stale `civitas-contrib` documents** — now unblocked,
   mark superseded, point at `civitas-io/fabrica` and its `pip install
   fabrica-context` name, note the MCP code's migration destination
   specifically (not just "see the new repo").
3. **Actually perform the `MCPClient` code migration + the `srt` rewrite**
   into `civitas-io/fabrica`, once real code-writing begins there —
   `BubblewrapSandbox` specifically does NOT migrate as-is (resolved in
   `mcp-integration.md`: replaced by cross-platform `srt`, not carried over
   Linux-only).
4. **No code exists yet anywhere in `civitas-io/fabrica`.** Scaffolding the
   actual `fabrica/` Python package (`pyproject.toml`, source layout) is the
   next phase-level step — `CivitasRuntime` is now resolved, so this is no
   longer blocked on that.
5. Everything in "What's explicitly left open elsewhere" above — still
   accurate, nothing there has been resolved by the contracts work.
6. `mcp-integration.md`'s open questions — **four of five resolved** through
   direct walkthrough (isolation mechanism -> `srt`; connection lifecycle ->
   eager, forced by `ToolManager.register()`'s contract; multi-server ->
   already sufficient; cache invalidation -> split into a hard guarantee that
   ships now and a soft one blocked on `Retriever`'s own open item). One
   genuinely still open: zero spike coverage for `MCPToolNamespace`
   end-to-end. A new platform-wide rule surfaced along the way, third
   instance of it now: **when a security/governance mechanism is
   unavailable, fail closed by default; the exception is always an
   explicit, greppable opt-in flag, never a silent fallback** (`allow_ungoverned`,
   now `allow_unsandboxed`).
7. `prompts.md`'s "Explored" survey has two items marked worth prioritizing
   that got promoted into the contract (cache-boundary, `PROMPT.md` format)
   and several marked correctly deferred — no action needed unless demand
   surfaces.
8. `RecencyCompactor`'s core mechanism is now spiked (see the arc section
   above) — the strategy is validated; `preserve_last_n=6` specifically is
   not. Next-level spike, if ever prioritized: vary N against a real
   token-budget boundary, not just the fixed window this spike used.
9. **New: `mcp-server.md` written** — Fabrica as an MCP server, the
   complementary opposite of `mcp-integration.md`'s client direction. Closes a
   claim `tool-execution.md` made and never designed. Key finding: the
   two-path token-efficiency thesis has to be preserved for EXTERNAL MCP
   clients too, not just internal models — exposing every registered
   tool/skill as its own flat MCP tool would reintroduce the exact O(N)
   schema-dump cost this project exists to eliminate, just shifted onto
   whoever calls in from outside. Resolved: five fixed, generic MCP tools
   (`fabrica_find`/`fabrica_run_code`/`fabrica_run_skill`/memory ops) plus
   `PromptManager` mapped directly onto MCP's native Prompts primitive.
   Bearer-token auth for HTTP/SSE (deliberately NOT mTLS, unlike
   `PresidiumClient` — different topology, many diverse third-party clients
   vs. one controlled service). A NEW connection-level auth layer sits in
   front of, not parallel to, existing per-call governance (`check_grant`
   reused unchanged once a connection resolves to an `agent_id`).
   `allow_weak_isolation_for_external_callers` is the FOURTH confirmed
   instance of the fail-closed-by-default-explicit-opt-in-to-bypass rule.
   Zero spike coverage, same honest flag as everywhere else this applies.

## Two more contracts written: `mcp-integration.md`, `mcp-server.md` — reasoning-trail detail; see "Current state" at the top for status

`docs/contracts/mcp-integration.md` (`MCPClient`/`MCPToolNamespace`) and
`docs/contracts/mcp-server.md` (`FabricaMCPServer`) are both done, formalizing
the two MCP-direction design docs. Two real findings from writing exact
signatures, not just transcription:

1. **A genuine implementation wrinkle in the client contract**: eager
   connection (required by `ToolManager.register()`'s existing contract)
   needs to happen in `__init__`, but `connect()` is a coroutine and Python
   constructors can't be `async`. Needs an async factory
   (`await MCPToolNamespace.create(client)`) or equivalent — not resolved,
   flagged honestly as something the design doc's sketch glossed over.
2. **A real gap in `contracts/prompts.md`, found and closed immediately**:
   writing `FabricaMCPServer`'s `prompts/list` handler required enumerating
   *all* registered prompt names, and `PromptStore`/`PromptManager` only had
   `list_versions(name)` — versions of a name already known. Added
   `list_names() -> list[str]` to both directly, rather than left dangling.

Eight contracts total now across the object model + MCP integration.
Remaining named debt: `mcp-server.md`'s `WeakIsolationError` only checks once
at construction (no live re-check if a service-mode deployment's tier changes
later), and multi-tenant HTTP deployments aren't stress-tested against shared
`SandboxPool`/`Retriever` state. Zero spike coverage for both, unchanged from
the design docs.
