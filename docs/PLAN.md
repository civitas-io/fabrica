# Plan

**Status:** Active tracking doc · **Last updated:** 2026-08
**Purpose:** the single ordered work queue. Phase 1 (reflection fixes) ships
first, in full, before Phase 2 (remaining backlog) starts. Within each phase,
items are sorted **easiest first, most complex last** — not by importance.
Detail for any item lives in the doc it links to; this doc tracks order and
status only, it does not re-derive reasoning already written down elsewhere.

Mark items `[x]` as they land, with the commit/PR they landed in.

---

## Phase 1 — Reflection fixes

**Status: complete (all six items, including the 6a/6b split).**

From [`self-reflection-report.md`](self-reflection-report.md). Fixed these
before anything else because two of them are actively misleading docs, one
was a decision left in limbo, and the last two were real product gaps
against named success metrics — all found by checking the code against the
vision, not invented fresh. Two items (4, 6a) were, as planned, walked
through directly rather than decided unilaterally. Two items (5, 6b) turned
out smaller in scope than originally written, once real investigation
replaced the original assumption -- both corrected inline rather than
quietly matching the original estimate. Phase 2 (the remaining backlog)
starts next.

1. [x] **Rewrite `README.md`** to reflect actual current state — all six
   object-model contracts done, `FirecrackerSandbox`/platform dispatch real,
   both MCP directions built, real test counts (195 local + 14 real-hardware),
   twelve spikes. Pure rewrite, no decisions required. *(§3.6)* — also fixed
   the "interface-first, `fabrica`/`fabrica-contrib`" claim and the "spans and
   audit events" claim to state honestly that both are designed, not built.
2. [x] **Correct `context-layer.md` and `docs/retrieval.md`'s `KeywordBackend`
   claim** — both currently assert Rust+PyO3 in the present tense; the real
   code is pure-Python `rank-bm25`. Restate as the real, deliberate v1
   decision ("ship the default, revisit if forced," no performance evidence
   yet justifies the tooling cost) instead of leaving the false claim
   standing. Pure doc correction, no code change. *(§3.2)* — also fixed the
   same stale claim in `contracts/retriever.md`, `architecture.md` (prose
   and image alt-text), and the baked-in `<text>` labels inside
   `assets/package-structure.svg` itself, found while checking for other
   stragglers rather than stopping at the two files named.
3. [x] **Add MCP client/server to `context-layer.md`'s "Scope: what Fabrica
   owns" table** — `mcp-server.md` already correctly re-checked and passed
   the "not a generic MCP proxy" boundary test; this is just making the
   scope table match what was already decided elsewhere. Pure doc addition.
   *(§3.5)* — also added a note distinguishing scope item 6 from the
   rejected "generic MCP proxy" row in the "does NOT own" table, and an
   honesty caveat next to the pre-existing "emits spans and audit events"
   claim (design intent, largely unbuilt — same finding as §3.3).
4. [x] **Decide on the `fabrica`/`fabrica-contrib` package split** —
   discussed directly rather than resolved unilaterally. **Decision:
   deliberately deferred until closer to a real release, not built now
   and not abandoned.** Reasoning: no external user yet for the
   zero-infra-install property to matter to in practice, and building the
   split before Tier 1 isolation, managed-sandbox adapters, and real
   memory-backend adapters exist would mean guessing its final shape
   before there's anything real to validate it against — each of those
   would otherwise need to retroactively fit a split decided too early.
   Documented in `context-layer.md` and `README.md` as a decided
   deferral, not left in limbo. Revisit before/at a real release, or the
   moment a real user's install footprint becomes a genuine complaint,
   whichever comes first. *(§3.1)*
5. [x] **Build real OTEL span emission across the nine spans named in
   `system-design.md §7`** — all nine real now (`src/fabrica/observability.py`).
   **Turned out to need no new dependency at all** (correcting the
   assumption this item was written with): `Tracer`/`Span` are structural
   Protocols matching `civitas.observability.tracer.Tracer`'s real,
   public shape exactly -- a real, important finding while building this:
   Civitas does NOT use OpenTelemetry's global `TracerProvider` registry,
   it propagates `trace_id`/`parent_span_id` explicitly via its own
   message-envelope fields, so a plain `opentelemetry.trace.get_tracer()`
   call would not have actually routed through Civitas's own span
   pipeline. `fabrica.observability` imports neither `opentelemetry` nor
   `civitas` -- real OTEL flows transitively through `civitas` itself
   (already a hard dependency) only once a caller supplies a real
   `Tracer`. Every component defaults to `NullTracer()` (real no-op,
   matching `NullPresidiumClient`/`NullCompactor`); `CivitasBridge` gained
   a `tracer` constructor parameter, deliberately NOT auto-constructing a
   real `civitas.observability.tracer.Tracer()` by default (real side
   effects -- an OTEL provider, a console exporter -- that would silently
   change every existing caller's behavior, this codebase's own test
   suite included). Real nested trace trees, not disconnected same-
   prefix spans: `fabrica.tool.code_mode.run`/`fabrica.skill.run` are the
   real parent of `fabrica.presidium.check_grant`,
   `fabrica.sandbox.acquire`, and `fabrica.sandbox.run`;
   `fabrica.tool.find`/`fabrica.skill.find` nest `fabrica.retriever.search`
   underneath. 15 new tests (6 unit, 5 full-object-graph integration
   tests via a real `CivitasBridge`-built `Fabrica`, including one denied-
   grant test proving `traced()` records and re-raises errors correctly
   without swallowing them, and one proving a REAL
   `civitas.observability.tracer.Tracer` satisfies the Protocol
   structurally with zero adapter code -- not just a hand-rolled fake).
   Two small real bugs found and fixed while wiring this up, not just
   assumed correct: `check_grant`'s span was missing its own documented
   `latency_ms` attribute; `None`-valued attributes (e.g. `Retriever
   .search()`'s optional `kind`) would have silently triggered per-call
   warnings against a real OTEL-backed span, now filtered in `traced()`
   before reaching the `Tracer`. *(§3.3, first half)*
6a. [x] **Credential injection into `Sandbox`** — walked through directly,
   as planned, before any code. **Resolved as a decision, not a build**:
   Fabrica builds no credential-injection mechanism into `Sandbox` at all.
   Investigated Tessera (`tsr`, a real, separately-built agent-blind
   credential broker already part of the Civitas toolchain) first, rather
   than designing from scratch. Real finding: Tessera's own interpreter/
   exec-wrapper denylist structurally refuses to inject a secret into a
   Python interpreter running caller-supplied code -- exactly what
   `Sandbox.execute()` is. Two independently-built systems agree
   credentials must never reach an interpreter about to run untrusted
   code. Validated end to end, not just argued: Fabrica's real, unmodified
   `MCPClient`/`MCPToolNamespace`/`ToolManager` stack composed with a
   real, unmodified `tsr mcp` process with **zero new Fabrica code** --
   model-generated code running inside a real `SubprocessSandbox` called
   a Tessera-backed tool, with only the redacted result crossing back
   ([SPIKE-tessera-credential-integration.md](../specs/archive/spikes/SPIKE-tessera-credential-integration.md),
   [`docs/credentials.md`](credentials.md)). Honest gap named, not
   Fabrica's to fix: Tessera's approval model requires a human present
   (`/dev/tty`/Touch ID) and refuses unattended/service-mode use -- a real
   limitation for Marcus's production persona, tracked as Tessera's own
   roadmap item, not Fabrica work.
6b. [x] **Real usage/budget metering** (`civitas-presidium-integration.md`'s
   metering-vs-enforcement design) -- **turned out to need no new
   integration point at all**, correcting this item's own original
   assumption. The consumption events ARE the item-5 spans -- this doc's
   own "emits standardized consumption events" language, taken literally:
   added the two real, missing attributes (`latency_ms` on
   `fabrica.tool.find`/`fabrica.skill.find`, `volume_bytes` -- real
   content byte length, not an item count -- on `fabrica.memory.write`/
   `search`). Proved the "checks before executing" enforcement half needs
   zero new code too: a real `PresidiumClient` returning `deny` for an
   over-budget scope already refuses the run before `SandboxPool.acquire()`
   via the existing `check_grant()` gate, proven directly (a dedicated
   test asserting zero `fabrica.sandbox.*` spans exist when denied for an
   explicitly budget-shaped reason, not just that an exception was
   raised).

   **One real, honest measurement gap found and deliberately left
   unmeasured, not papered over**: `Sandbox`'s "memory bytes" dimension.
   Measured empirically before attempting to add it: `resource.getrusage
   (RUSAGE_CHILDREN).ru_maxrss` is a monotonically non-decreasing
   high-water-mark across the WHOLE PARENT PROCESS's lifetime, not a
   per-call delta -- confirmed directly (a 20MB child run measured
   *after* a 200MB one reports the 200MB figure, not its own). Reporting
   this naively as a per-run number would have been silently wrong, not
   just imprecise -- worse than the honest gap it's left as, matching the
   same standard already applied to `FirecrackerSandbox.cpu_seconds=0.0`.
   A correct fix needs real per-process sampling (`/proc/<pid>/status`
   polling or a `psutil` dependency), not attempted here.

   4 new tests. *(§3.3, second half)*

---

## Phase 2 — Remaining backlog

Everything catalogued in the "what's remaining" evaluation, re-sorted here
by complexity. Detail and full reasoning for each item lives in the contract
doc it's already named in — not repeated here.

### Easy

**Status: complete (all six items, 7–12).**

7. [x] `civitas-bridge.md` open item 2: `build()`'s idempotency —
   **decided: cached, a second call returns the same `Fabrica` instance**
   (avoids a second live `SandboxPool`/warm pool, and, in service mode, a
   second `request_state_persistence()` call per component with unclear
   concurrent-handle semantics). Updated the one existing test that had
   documented the opposite as "current behavior" back when this was
   genuinely undecided.
8. [x] `civitas-bridge.md` open item 1: validate `dynamic_supervisor_name`
   upfront — **decided: yes**, via the real, public
   `civitas.runtime.Runtime.get_agent()` lookup (added to the
   `CivitasRuntime` Protocol), raising a new `SupervisorNotFoundError` at
   construction. Found and fixed a real, pre-existing test gap while
   adding this: one test used a service-mode `CivitasBridge` against a
   topology that never actually defined the named supervisor.
9. [x] `managers.md` open item 1: `find()`'s `kind` override — **decided:
   no override, stays fixed per manager.** A caller genuinely needing
   "search everything" already has a real way to get it --
   `Retriever.search(query, kind=None)` directly. No code change: this
   was already the implemented behavior.
10. [x] `sandbox.md` open item 3: `on_tool_call`'s own timeout — **decided:
    yes**, a new optional `tool_call_timeout` on `Sandbox.execute()`/
    `SandboxPool.run()`, threaded through both managers, raising a new
    `SandboxToolCallTimeoutError` when it fires. Found and fixed two real
    bugs in `SubprocessSandbox` while implementing this, not assumed
    correct on the first pass: (1) a hung tool call was never actually
    observed by `execute()` at all before this, silently consuming the
    entire overall timeout budget first; (2) a genuinely nasty cleanup
    bug where awaiting an already-exception-holding task in `finally`
    re-raised that same exception a second time, silently skipping every
    remaining cleanup statement -- invisible in normal test runs because
    the re-raised exception happened to match what the test already
    expected, only surfacing as a real process hang during garbage
    collection at shutdown (confirmed via a real stack dump). 5 new tests
    across both backends plus `SandboxPool`'s pass-through.
11. [x] `prompts.md` open items 3–4: `PromptTemplate.content` size ceiling
    (256KB, `MAX_PROMPT_CONTENT_BYTES`) and `cache_boundary` validation --
    **decided: reject, don't silently pass through or truncate**, both
    enforced in `PromptTemplate.__post_init__` so every construction path
    gets the guarantee for free. New `PromptTooLargeError`/
    `InvalidCacheBoundaryError`. Real bug found and fixed alongside this:
    `InMemoryPromptStore.put()`'s broad `except Exception` was swallowing
    these into a generic `PromptBackendError`, indistinguishable from an
    actual storage failure -- fixed to let them propagate unwrapped. 3
    existing tests used deliberately out-of-range `cache_boundary` values
    (valid before this validation existed) -- fixed to use real, in-range
    values rather than loosened to accommodate the old fixtures. 5 new
    tests.
12. [x] `memory.md` open item 2: `WorkingMemoryQuotaExceeded`'s default
    ceiling -- **found already resolved in code**, just not marked as
    such in the contract: `DEFAULT_QUOTA_BYTES = 256 * 1024` already
    ships with an honest "placeholder, not validated" docstring, and
    `quota_bytes` is already a real, overridable constructor parameter,
    already tested. Pure documentation fix -- no code change needed.

### Medium

13. [ ] `retriever.md` open item 1: eager-cache invalidation strategy when
    an eager item is deregistered mid-flight.
14. [ ] `retriever.md` open item 2: decide batch atomicity
    (all-or-nothing vs. best-effort) for large `register`/`deregister` calls.
15. [ ] `memory.md` open item 1: the single-message-exceeds-budget edge case
    in `RecencyCompactor` — needs a product decision, not a contract default.
16. [ ] `prompts.md` open item 1: `PromptManager`'s cache eviction policy
    (size ceiling, TTL, or unbounded) — needs a decision before it matters
    at real scale.
17. [ ] `FirecrackerSandbox` real per-VM CPU accounting — wire Firecracker's
    own metrics API; `cpu_seconds` is currently honestly `0.0`.
18. [ ] A real, minimal, purpose-built Firecracker rootfs image — replacing
    the current general-purpose Ubuntu 24.04 image + baked-in shim.

### Complex

19. [ ] Tier 1 isolation backend (`gVisor`/`srt` on Linux) — the natural
    next isolation milestone once `FabricaMCPServer(kind="http")` sees real
    external traffic; not urgent otherwise since Tier 2 already clears the
    bar Tier 1 would.
20. [ ] `FirecrackerSandbox` snapshot/restore — combining an already-live
    `vsock` connection with snapshot restore is a genuinely unvalidated
    combination; needs its own spike before implementation.
21. [ ] `jailer` integration for `FirecrackerSandbox` — real defense-in-depth
    hardening, unexplored in both Firecracker spikes so far.
22. [ ] Managers as supervised `GenServer`s ("self-healing pool") — real
    structural work: Civitas's dynamic-spawn reconstructs classes from a
    dotted path with only `name`, incompatible with today's DI-constructed
    managers. Needs either a Civitas-side change or a real redesign of how
    managers are constructed in service mode — **walk through with the
    user before starting**, this is an architecture-level call.
23. [ ] Managed-provider adapters (E2B/Modal/AWS/Azure/GCP) — interfaces
    already designed (`managed-sandbox.md`); real implementation work once
    started.
24. [ ] `TunnelProvider` concrete backends (Tailscale Funnel, Cloudflare
    Tunnel, ngrok) — Protocol designed, none implemented.

### Blocked — not sequenced by complexity, simply not actionable right now

- [ ] `PresidiumClient`'s real REST+mTLS client — no real Presidium HTTP
  server exists anywhere to build/validate against.
- [ ] Managed-provider adapters' real credentials (E2B/Modal/AWS/Azure/GCP)
  — also deliberately deprioritized behind self-hosted Tier 2 regardless.

### Deliberately out of scope for this plan — already correctly deferred, revisit only if forced

Third-party skill trust/signing, log tamper-evidence, other memory backends
beyond Mem0, `SKILL.md` optional fields/bundled resources, Windows Tier 1,
macOS/Windows Tier 2 `vsock` equivalents (`VZVirtioSocketDevice`/`AF_HYPERV`).
Named in `problem-definition.md`/`HANDOFF.md` already — not re-litigated here.
