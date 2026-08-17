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

From [`self-reflection-report.md`](self-reflection-report.md). Fixing these
before anything else because two of them are actively misleading docs, one
is a decision left in limbo, and the last two are real product gaps against
named success metrics — all found by checking the code against the vision,
not invented fresh.

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
6. [ ] **Design and build credential injection into `Sandbox`, plus the
   usage/budget-metering half of `civitas-presidium-integration.md`** — the
   most complex item in this phase: no existing mechanism to extend, real
   security surface (a credential reaching sandboxed, untrusted code), and
   a genuinely new integration point with Presidium's budget model.
   Depends on item 5 landing first (the span/metering pipe needs to exist
   before usage events have anywhere to go). **Walk through the credential-
   injection design directly before building** — same reasoning as item 4:
   this is exactly the class of decision this project has always paused
   on rather than making unilaterally. *(§3.3, second half)*

---

## Phase 2 — Remaining backlog

Everything catalogued in the "what's remaining" evaluation, re-sorted here
by complexity. Detail and full reasoning for each item lives in the contract
doc it's already named in — not repeated here.

### Easy

7. [ ] `civitas-bridge.md` open item 2: decide `build()`'s idempotency on
   repeated calls (fresh graph each time / cached / raise) — small decision,
   small implementation either way.
8. [ ] `civitas-bridge.md` open item 1: decide whether to validate
   `dynamic_supervisor_name` upfront vs. let the first `spawn()` surface a
   misconfiguration.
9. [ ] `managers.md` open item 1: decide whether `find()` should accept a
   `kind` override or always fix it per manager.
10. [ ] `sandbox.md` open item 3: decide whether `on_tool_call` needs its
    own timeout distinct from `run()`'s overall one.
11. [ ] `prompts.md` open items 3–4: `PromptTemplate.content` size ceiling;
    `cache_boundary` validation against actual content length.
12. [ ] `memory.md` open item 2: `WorkingMemoryQuotaExceeded`'s default
    ceiling — currently a guess (256KB suggested), no real usage pattern to
    validate against yet; pick a value and state it's a placeholder.

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
