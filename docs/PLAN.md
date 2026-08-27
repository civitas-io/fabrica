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
   ([SPIKE-tessera-credential-integration.md](https://github.com/civitas-io/fabrica/blob/main/specs/archive/spikes/SPIKE-tessera-credential-integration.md),
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

13. [x] `retriever.md` open item 1: eager-cache invalidation strategy when
    an eager item is deregistered mid-flight -- **decided: invalidate
    immediately.** `deregister()` now pops from `Retriever`'s own
    bookkeeping (`list_eager()`'s real data source) BEFORE attempting
    either backend's best-effort removal, not after -- closes the
    mid-flight staleness window the contract named, without touching
    `search()`'s separate (and unchanged) eventual-consistency behavior.
    2 new tests.
14. [x] `retriever.md` open item 2: decide batch atomicity
    (all-or-nothing vs. best-effort) for large `register`/`deregister`
    calls -- **decided: two different guarantees at two different
    stages**, not one blanket answer. Duplicate-id checking across the
    WHOLE batch happens before either backend is touched (real
    all-or-nothing there); each backend's own `add()` call for the whole
    list is best-effort per item past that point, since
    `RetrieverBackend.add() -> None` has no way to report which items in
    a batch succeeded -- matches what the only real backend
    (`KeywordBackend`) already does. 1 new test proving the duplicate
    check's own all-or-nothing guarantee specifically.
15. [x] `memory.md` open item 1: the single-message-exceeds-budget edge case
    in `RecencyCompactor` -- **found already resolved in code**, same
    shape as item 12: `_select_preserved` already folds an oversized
    single message into the summarized set (zero preserved verbatim),
    already tested
    (`test_single_message_exceeding_budget_preserves_nothing_verbatim`).
    Decided and documented explicitly rather than left "unresolved":
    fold into the summary (relying on the injected `Summarizer` to
    compress it, its actual designed job), not truncate (would need a
    tokenizer dependency this project deliberately avoids) or drop
    silently (loses information without signaling). `CompactionResult
    .preserved == []` is itself the observable signal, no new field
    needed. Pure documentation fix -- no code change needed.
16. [x] `prompts.md` open item 1: `PromptManager`'s cache eviction policy
    (size ceiling, TTL, or unbounded) -- **found already resolved in
    code**, same shape as items 12 and 15: `PromptManager`'s own
    docstring already stated "unbounded in-process cache" as a real,
    reasoned decision (prompt catalogs expected to be small and curated,
    unlike tool/skill catalogs or memory), just never reflected in the
    contract's own open-items list. Pure documentation fix -- no code
    change, no new test (there is no meaningful way to test the absence
    of an eviction mechanism beyond what `test_get_is_cached_avoids
    _backend_round_trip` already proves about the cache itself).
17. [x] `FirecrackerSandbox` real per-VM CPU accounting -- **resolved, but
    NOT via Firecracker's own metrics API as originally framed**:
    checked directly against the real, bundled OpenAPI spec first --
    `/metrics` is write-only (configures a periodic-dump named pipe, not
    a queryable CPU-seconds value) -- so that mechanism was never
    actually going to work. Real fix: read `/proc/<firecracker_pid>
    /stat` from the host (same technique `libvirt`/`virsh domstats`
    use), since Firecracker's vCPU(s) run as threads within one process,
    not separate children -- that process's aggregate utime+stime
    already includes all real guest CPU execution. Reported as a delta
    against the same process's own reading at execute()'s start, not a
    lifetime total (unlike the memory-bytes dimension, which stayed
    deliberately unmeasured for exactly that reason -- this metric
    doesn't have that problem). Verified for real on the homelab before
    writing permanent tests: a CPU-bound guest loop measured ~2.81s
    delta CPU time closely matching its own ~2.81s wall-clock duration;
    a trivial task measured ~0.000s. 2 new tests, verified stable 3x on
    real hardware alongside the existing 18 hardware-gated tests,
    filesystem confirmed clean afterward.
18. [x] A real, minimal, purpose-built Firecracker rootfs image --
    **done**, no new `sudo` scope needed. Ubuntu 24.04 + `python3` only
    (no systemd, no other packages), built via a real `docker build`
    /`export` + `mke2fs -t ext4 -d` (entirely userspace -- no mount, no
    loop device, no root for the base-image-creation step itself; only
    the existing, already-scoped mount/cp step for baking the guest
    shim in afterward). New: `scripts/build_firecracker_minimal_base.sh`,
    `scripts/firecracker-minimal-base.Dockerfile`.
    **A real dead end tried and rejected first, not assumed correct**:
    the obvious choice, the official `python:3.12-slim` Docker image,
    installs Python at `/usr/local/bin/python3.12`, not the fixed
    `/usr/bin/python3` `FirecrackerSandbox`'s kernel boot args require --
    confirmed via a real kernel panic on real hardware (`Requested init
    /usr/bin/python3 failed (error -2)`), not caught by inspection.
    Plain `ubuntu:24.04` + `apt-get install python3` installs the
    standard distro way instead, matching the fixed boot args with zero
    code change.
    **Real, measured result** (homelab, real KVM/Firecracker): apparent
    image size 1.0G -> 300M; actual on-disk size 170M -> 60M; the
    per-instance rootfs copy `boot_clean()` does for every sandbox
    instance dropped from a consistent ~945-955ms to ~265-268ms (3 runs
    each, same session/disk) -- ~3.5x faster, roughly proportional to
    file size since `shutil.copyfile()` doesn't preserve sparseness.
    End-to-end `boot_clean()` (fair back-to-back comparison, 3 runs
    each): ~1910ms avg -> ~1205ms avg, a real ~37% reduction.
    **Stated honestly, not oversold**: guest kernel BOOT TIME itself did
    NOT meaningfully improve (dominated by kernel init/`devtmpfs`/Python
    interpreter startup, not rootfs size) -- a real disk-footprint/copy-
    time win, not a boot-latency win; the commonly-cited "~125ms boot"
    figures likely need eliminating CPython's own interpreter-startup
    cost as PID 1 entirely (a compiled/static init), separate, uncounted
    work not done here.
    Verified end to end on real hardware before any script/Dockerfile
    existed: real stdlib imports (`re`/`hashlib`/`datetime`/`json`/
    `base64`/`itertools`/`collections`/`math`/`random`/`uuid`/`urllib
    .parse`), `/dev/null`/`/dev/urandom` both exist and are readable
    (`devtmpfs` auto-mount, confirmed via console log, not assumed).
    The full existing 16-test `test_firecracker_backend.py` suite (CPU
    accounting, network isolation, timeout handling, the real tool-call
    boundary crossing) then re-run against a base image built by the
    FINAL script itself, not just the manual steps that found it first --
    16/16 passed. Documented in `docs/deployment/firecracker-rootfs.md`
    (new "Building a minimal base image" section with full numbers),
    `docs/contracts/sandbox.md`, `docs/isolation.md`.

### Complex

19. [x] Tier 1 isolation backend — **done, via `srt`, not `gVisor`**
    (`SrtSandbox`, e79f51e, done directly by the user in a parallel
    session, reviewed and one real bug fixed afterward — see item 19a
    below). Real, OS-level, default-deny network enforcement
    (Seatbelt/bubblewrap+netns/WFP per platform), live-verified on macOS
    (allowlisted domain reachable, non-allowlisted domain gets a real
    proxy-level 403, `~/.ssh` read denied, guest-shim ZMQ bridge still
    works through the restriction) — Linux/Windows untested, `srt`
    documents support for both but neither exercised here, stated
    honestly rather than assumed. One gVisor-shaped implementation
    avoided entirely: `srt` also covers Linux (bubblewrap+netns), so a
    second, separate Tier 1 backend for that platform is deferred until
    `srt`'s own Linux support is verified, not built reflexively.
19a. [x] Reviewed the SrtSandbox work end to end (user's own request,
    "double check the srt work") and found one real, confirmed bug:
    `SrtSandbox.__init__` allocates a `/tmp` directory shared across
    every handle an instance ever produces, and `terminate()` (per-
    handle) could never safely remove it — 460+ leaked directories
    confirmed accumulated in `/tmp` from ordinary dev/test iteration
    before the fix. Resolved: `Sandbox` gained a fourth lifecycle
    method, `close()`, for backend-instance-level cleanup, documented on
    the Protocol itself per the user's explicit request so future
    backends pick up the rule by construction, not by rediscovering the
    same leak. A second instance of the same bug class was found and
    fixed across 7 more test call sites in the same pass
    (`fabrica.close()` never called, harmless under Tier 0, a real leak
    once dispatch could select `SrtSandbox`). Verified concretely: zero
    leaked directories after 3 clean full-suite runs, where 460+ existed
    before. Full detail: `docs/contracts/sandbox.md`'s own "Real
    addition: `close()` on `Sandbox`" section, commit b11d484.
19b. [x] `FirecrackerSandbox` network isolation -- **verified for real,
    not just implied.** User's own question ("does the sandbox
    implementations also have network isolation?") surfaced a real gap:
    `FirecrackerSandbox` never calls Firecracker's `/network-interfaces`
    API, so the guest boots with no network device at all -- always the
    design intent (`isolation.md`'s vsock rationale), but never actually
    tested the way `SrtSandbox`'s network denial was. Tested for real on
    the homelab (`kodiak@darkenergy`): a raw socket connect fails
    immediately with `OSError: [Errno 101] Network is unreachable`; DNS
    resolution fails immediately with `socket.gaierror: [Errno -3]
    Temporary failure in name resolution`. Structurally stronger than
    `SrtSandbox`'s policy-based allow-only enforcement -- no interface to
    misconfigure at all, not a firewall rule to bypass. 2 new tests
    (`test_execute_has_no_network_path_at_all`,
    `test_execute_dns_resolution_also_has_no_path`), verified 3x stable
    on real hardware alongside the existing 16 hardware-gated tests,
    filesystem confirmed clean afterward (no leftover `/tmp/fc-*` files).
    Documented in `docs/contracts/sandbox.md` and `docs/isolation.md`'s
    tier table.
20. [x] `FirecrackerSandbox` snapshot/restore -- **spike done, real
    implementation is a separate follow-up (20a).** Two real, concrete
    findings on real hardware, not inference: (1) Firecracker's own
    vsock device binds a Unix socket at the base `uds_path` -- `SIGKILL`
    never cleans it up, so restoring into a fresh process fails
    immediately with `EADDRINUSE` unless that stale file is deleted
    first; (2) the guest kernel-panics on resume as shipped, but for a
    real, understood, FIXABLE reason: the guest's blocked `recv()`
    correctly gets a real `ConnectionResetError` (the old peer genuinely
    doesn't exist anymore), and `_firecracker_guest_shim.py` has zero
    error handling around it, so the unhandled exception kills PID 1 and
    Linux panics. **Verified the fix actually works, not just diagnosed
    the problem**: a throwaway patched shim with a real reconnect loop
    (catch `OSError`, open a fresh vsock socket, retry) successfully
    reconnected and re-signaled `ready` to a fresh process's fresh
    listener after a real snapshot/restore cycle -- no panic, no manual
    intervention. The combination is real and buildable; it just needs
    guest-shim hardening that was never built (the shim was never
    designed against restore in the first place -- finding that out was
    this spike's whole job). Full detail, including the exact console
    log excerpts: `specs/archive/spikes/SPIKE-firecracker-snapshot-
    restore-vsock-combination.md`.
20a. [x] Real implementation -- **done**. Ported real, production
    reconnect logic into `_firecracker_guest_shim.py` itself (bounded --
    200 attempts, no sleep for the first few, then a real but small
    backoff -- not the spike's placeholder). `FirecrackerSandbox` gained
    `use_snapshot_restore: bool = False` (opt-in, default preserves v1's
    exact cold-boot behavior, zero change for any existing caller): the
    FIRST `boot_clean()` lazily cold-boots ONE throwaway instance purely
    to create a reusable golden snapshot (paying the cold-boot cost
    exactly once, protected by a double-checked lock against concurrent
    racing callers), then every restore -- including that very first
    served instance -- uses `/snapshot/load` instead.
    **No `SandboxPool` changes needed at all** -- it just calls
    `backend.boot_clean()`; the whole mechanism is internal to
    `FirecrackerSandbox`.
    **One more real unknown found and resolved before writing any
    "real" code, not assumed**: does the golden snapshot's embedded,
    fixed vsock path force every restored instance to collide? No --
    checked the real, bundled OpenAPI spec and found `vsock_override`,
    a real, documented `/snapshot/load` parameter letting every restored
    instance get its own vsock path. Verified on real hardware: two
    concurrent instances, same snapshot, distinct `vsock_override`
    paths, both restored in ~8.5-8.8ms, both correctly reconnected.
    **A second, more serious unknown also found and resolved, not
    glossed over**: no equivalent override exists for the ROOTFS block
    device (confirmed against the same spec) -- every restored instance
    references the SAME golden rootfs file. Tested this directly for
    real, not assumed safe: two concurrent restored instances each wrote
    a distinct, deliberately-chosen file to their own guest filesystem
    and read it back -- both correct, no cross-contamination. Documented
    precisely WHY this holds for Fabrica's actual usage (each restored
    instance is used for exactly one `execute()` then terminated, so
    nothing ever depends on the shared file's own on-disk state
    afterward) rather than claimed as generally safe.
    8 new tests (a working restored handle; the second `boot_clean()`
    call measurably faster than the first, proving restore is really
    happening; concurrent-instance isolation; a real tool-call round
    trip on a restored instance; `terminate()` never deleting the shared
    golden rootfs; `close()` removing the golden snapshot files;
    `close()` safe with no snapshot ever created; the
    `use_snapshot_restore=False` default provably unchanged). All 24
    tests in the file (16 existing + 8 new) verified passing on real
    hardware, stable across repeated runs, filesystem confirmed clean
    afterward (0 leftover `/tmp/fc-*` files).
    Only the pre-request "blocked waiting for `code`" state is covered
    -- a guest snapshotted mid-tool-call would need the same treatment
    if that's ever wanted, named as real, separate, not-yet-needed
    future work, not silently assumed covered.
21. [x] `jailer` integration for `FirecrackerSandbox` -- **DONE, real,
    shipped, opt-in (`use_jailer=True`), cold-boot only.** Decided
    directly with the user: not combined with `use_snapshot_restore`
    ("security over optimization") -- raises `SandboxConfigurationError`
    at construction time if both are set.
    Real infra set up and validated on the homelab: a dedicated
    `fc-jail` user/group (uid=gid=61000), `/srv/jailer` chroot base
    (`711`, traverse-only), and FOUR real, scoped sudoers rules
    (start/terminate/stage/cleanup), installed via a real, idempotent
    bootstrap script (`scripts/setup_firecracker_jailer.sh`).
    **The hard problem -- vsock inside a directory jailer locks to
    `700 fc-jail:fc-jail` -- is SOLVED and validated end to end on real
    hardware, with a real guest booting through the real jail boundary**:
    bind+listen the vsock socket as the invoking user BEFORE jailer
    runs (while the directory is still writable), explicitly
    `chmod(0o777)` it, then rely on the already-open file descriptor
    surviving jailer's subsequent lockdown. No sudo rule was needed for
    this specifically. **The second problem (the API socket, bound by
    firecracker itself as fc-jail) is resolved via Firecracker's own
    `--config-file` mechanism** -- schema confirmed directly against
    Firecracker v1.16.1's own source, empirically validated end to end:
    boot configuration (including vsock) goes into one static JSON file
    written before `jailer` runs, eliminating the need for any runtime
    API call, and therefore any further sudo grant, for boot itself. A
    real, previously-unanticipated 4th sudo rule WAS needed for cleanup
    (removing a terminated jail's on-disk footprint) -- scoped via 32
    repetitions of the `[0-9a-f]` character class against the real
    `uuid4().hex` instance-id format, not a `*` wildcard (which
    sudoers' fnmatch-style globbing would treat as path-traversal-
    permissive).
    **Full research trail, every empirical finding, the confirmed
    schema, and the real implementation**:
    `specs/archive/spikes/SPIKE-firecracker-jailer-vsock-integration.md`.
    Implemented in `src/fabrica/sandbox/firecracker_backend.py`
    (`_boot_jailed_instance`, `_terminate_jailed_instance`), 5 new tests
    in `tests/sandbox/test_firecracker_backend.py` (hardware-gated) plus
    5 pure construction/health-check tests in the new
    `tests/sandbox/test_firecracker_jailer_config.py` (found and fixed a
    real test-authoring bug along the way: the main test file's own
    module-level skip-if-no-hardware marker was silently skipping tests
    that needed no hardware at all -- moved to a separate file so they
    actually run everywhere). Verified 3x stable on real hardware, zero
    leaked processes or files each time. `docs/contracts/sandbox.md` and
    `docs/isolation.md` both updated with the full mechanism.
22. [x] Managers as supervised `GenServer`s ("self-healing pool") --
    **walked through directly with the user (Civitas's own maintainer),
    resolved as a documented finding, not built.** Confirmed the
    technical blocker against real Civitas source first: `spawn()`
    reconstructs a class from a dotted path via `agent_class(name=
    child_name)` only, no path for a manager holding live constructor-
    injected dependencies to survive it, and `spawn()` itself never
    returns a reference to inject them afterward either.
    Then went further than the technical blocker: the maintainer
    revealed `class_path`/`name`/`config` was never meant as a
    permanent contract -- an early stand-in for a much bigger,
    genuinely unresolved idea (real process-state migration/
    "teleportation" across nodes, not just restart-from-config). Tested
    Fabrica's own managers against whether they're a good motivating
    case for THAT bigger capability, and concluded they aren't:
    `SandboxPool`'s only genuinely interesting state (live warm handles
    -- real VMs/subprocess PIDs/sockets) is physically host-bound and
    cannot be migrated regardless of technology; every other manager's
    state is either trivially re-derivable from an external source of
    truth or already delegated to a real durable backend. Fabrica's
    real, narrower need (`SandboxPool`'s bookkeeping wedging from an
    undiscovered bug) is a process-crash-recovery problem, already
    substantially addressed if the embedding process sits under
    ordinary Civitas supervision (a plain static child, no dynamic
    spawn needed) -- ordinary restart already cold-boots a fresh
    `Fabrica`/`SandboxPool`.
    **A real, separate, stale-doc finding surfaced along the way**:
    `system-design.md`'s own component matrix (§4) still labeled every
    manager "GenServer" under service mode -- silently contradicting
    `contracts/civitas-bridge.md`'s own, already-correct account of the
    same finding, which had never actually been back-ported to the
    matrix. Fixed: the matrix now states the real, current shape
    (plain constructor-injected objects in both modes) directly.
    **Not closed as "unneeded", recorded as a documented finding with
    concrete revisit triggers** -- this project has no real production
    experience under real failure conditions yet: revisit if (a) real
    production shows process-level restart granularity is genuinely too
    coarse, or (b) Civitas's own migration/"teleportation" concept gets
    built for a better-motivated use case elsewhere. Full reasoning:
    `system-design.md`'s own "Finding: managers as supervised
    GenServers, investigated but not built" section.
23. [ ] Managed-provider adapters (E2B/Modal/AWS/Azure/GCP) — interfaces
    already designed (`managed-sandbox.md`); real implementation work once
    started.
24. [x] `TunnelProvider` concrete backends -- **DONE for priorities 1
    and 2 (Tailscale Funnel, Cloudflare Tunnel), both real, shipped,
    credential-free, validated end to end on real hardware** (real
    public URLs, real curl round trips, real access logs proving
    requests crossed the tunnel). `src/fabrica/tunnel/` -- new package,
    `TunnelProvider` Protocol, `TailscaleTunnelProvider`,
    `CloudflareTunnelProvider`, `select_tunnel_provider()` dispatch
    (mirrors `select_sandbox_backend()`'s own shape). A real Protocol
    addition found necessary during implementation: `is_available()`,
    alongside `start()`/`stop()` -- lets priority-ordered selection pick
    the first backend that will actually work, not just the first one
    merely installed. `NgrokTunnelProvider` (priority 3) NOT
    implemented -- needs a paid/free-tier account for anything beyond a
    very short session, per this contract's own already-stated
    adoption-friction note; not revisited unless a real deployment needs
    it and the first two are both unavailable. Real findings from
    `CloudflareTunnelProvider`'s implementation (four rounds of genuine
    end-to-end testing, not assumed from --help text): cloudflared's own
    "may take some time to be reachable" warning is real, not a hedge;
    the reachable-vs-not gap is itself variable and can exceed 30s; a
    specific quick-tunnel subdomain can sometimes never become reachable
    at all (matches Cloudflare's own "no uptime guarantee" disclaimer
    for account-less tunnels) -- fixed with a bounded retry using a
    fresh subdomain, not a longer wait against one already-degraded
    attempt; and a real bug where any non-connection-error curl status
    was wrongly treated as "reachable" (Cloudflare's own edge returns a
    real 530 when the tunnel-to-origin path itself isn't working yet).
    12 new tests (`tests/tunnel/`), full mechanism and every finding in
    `docs/contracts/managed-sandbox.md`'s own "real, shipped
    implementation" section.

### Added mid-session, not part of the original backlog -- costing/billing data

User question after item 17 ("should we consider other costing
parameters to track? This may help with billing and profit?") -- walked
through directly rather than decided unilaterally, per this project's
own established norm for market-positioning-relevant decisions. Landed
scope: **Fabrica captures and emits real numbers; billing, pricing, and
policy stay Presidium's job entirely** (`system-design.md §7`'s
existing "Fabrica emits, Presidium/Civitas consumes" boundary,
reaffirmed, not a new one invented for this).

25. [x] Context-footprint metering (`civitas-presidium-integration.md`'s
    usage/budget dimension) extended from `MemoryManager` (already real)
    to `ToolManager.find()`/`SkillManager.find()` and `PromptManager`.
    `fabrica.tool.find`/`fabrica.skill.find` gained `volume_bytes` (real
    `Indexable.description` bytes returned -- the field `retriever
    /types.py` itself calls "the only field actually embedded/matched",
    not a full serialized object). `PromptManager` gained `tracer` DI
    entirely -- a bigger gap than a missing attribute, since it emitted
    NOTHING before this (`system-design.md §7`'s span table never listed
    it at all): `fabrica.prompt.get`/`fabrica.prompt.put`, with
    `volume_bytes`, a real `cache_hit` boolean (mirroring `SandboxPool
    .acquire()`'s `warm_hit`), `prompt_name`, `version`.
    **Two real things found while implementing, not assumed correct**:
    (1) `traced()`'s own second positional parameter is itself called
    `name` (the span's name) -- passing the prompt's `name` as a keyword
    attribute collided with it, caught by mypy AND a failing test on the
    first attempt, fixed by using `prompt_name` instead; (2) a genuinely
    correct existing behavior, not a bug: `get(name)` right after
    `put(name, ...)` is a real cache MISS, not a hit -- `put()` only
    populates the specific version's cache entry, explicitly popping
    `(name, None)`'s "latest" alias rather than repopulating it. Also
    found and fixed: `CivitasBridge.build()` was wiring `tracer` through
    to `MemoryManager` but NOT `PromptManager` -- fixed alongside this,
    not a separate follow-up. 11 new tests across unit and integration
    levels. **Deliberately NOT done in this pass, named as bigger,
    separate work**: measuring `ToolNamespace.stubs()`'s own real context
    footprint (needs a new `ToolManager` capability, not just an
    instrumentation add -- `stubs()` isn't exposed through `ToolManager`
    at all today); a real "tokens saved vs. a naive full-catalog dump"
    number (needs a counterfactual baseline measurement in addition to
    what's actually returned -- a real feature built on top of this,
    not a rename of it).

### Added post-Phase-2 — documentation reliability audit (LLM council, 2026-08-27)

Same council-reviewed documentation-reliability exercise already run on `python-civitas` and
`presidium` (see those repos' own `docs/milestones.md`/`docs/log.md`), extended here. The
council's own verdict on Fabrica specifically: the single highest-leverage gap across all three
repos audited is that Fabrica had **no `AGENTS.md` at all** — worse than python-civitas's
910-line bloated one, since a bloated file at least gives an agent a signal to push back against;
zero file means every session starts from scratch. Full council transcript not persisted; verdict
acted on directly.

26. [x] **Wrote `AGENTS.md`** — a 102-line pure router (repo map, the Design/Contract
    precedence rule, pointers to `HANDOFF.md`/`PLAN.md`/the two self-audit docs), not reference
    content, per the council's explicit warning against re-deriving SDK reference material here.
27. [x] **Closed the Design→Contract naming-collision gap the council flagged as a real risk,
    not cosmetic.** Verified directly: `docs/contracts/{mcp-integration,mcp-server,memory,
    prompts}.md` all already linked back to their Design doc ("Depends on: `X.md` (`../X.md`)"),
    but none of the four Design docs linked forward to their Contract counterpart — an agent
    reading `docs/memory.md` cold had no signal a more current, implementation-ready
    `docs/contracts/memory.md` existed. Added a `**Formalized by:**` pointer to all four Design
    docs. Backed with a mechanical check, not just prose, per the council's explicit finding that
    every fix on the table was a soft convention identical to the one that caused presidium's
    staleness problem: new `tests/test_docs_structure.py` asserts both directions of the link
    for every same-named `docs/X.md`/`docs/contracts/X.md` pair, so this can't silently rot again.
28. [x] **Verified, not trusted, the two things the council flagged as claims rather than proof**:
    (1) the README's "~79% cheaper" code-mode claim — confirmed against
    `specs/archive/spikes/SPIKE-code-mode-execution.md`'s real 3-run token counts (79.5%/79.2%/
    79.0% reduction, honestly caveated against a larger, non-comparable published figure). (2) A
    sampled `docs/critique.md` "Resolved" item (A1, the Firecracker boot-time figure) — confirmed
    the fix actually landed in `docs/isolation.md` with the real measured numbers, not just
    marked Resolved in the audit doc itself.
29. [x] **Real, adjacent bug found and fixed while writing `AGENTS.md`'s source-layout section**:
    `src/fabrica/__init__.py`'s `__version__` was a hardcoded `"0.1.0"` literal, never updated
    since 2026-08-21's first release — `pyproject.toml` has been at `0.6.0` for multiple releases.
    `import fabrica; fabrica.__version__` reported the wrong version to any real caller. Fixed to
    read from installed package metadata (`importlib.metadata.version("fabrica-context")`), the
    same mechanical-not-prose fix philosophy as item 27 — this can't drift again either, since
    there's no second literal to forget to update.

**Deliberately not done**, per the council's own recommendation to not let this turn into new,
unrelated work: no CI enforcement for `docs/critique.md`/`docs/self-reflection-report.md`'s
"Resolved" claims going forward (an AGENTS.md note asking to spot-check on touch is the interim
answer); no mkdocs/docs site (the council explicitly ruled this out — no site exists to protect,
building one is a separate project); no org-wide RFC proposing Fabrica's Design/Contract or
self-reflection conventions to the other repos (a real idea, raised and explicitly rejected by
every peer reviewer in the council as scope creep ahead of verifying the thing being exported).

### Blocked — not sequenced by complexity, simply not actionable right now

- [x] ~~`PresidiumClient`'s real REST+mTLS client~~ **DONE, 2026-08-23/24,
  v0.2.0.** `civitas-io/presidium` shipped a real M7 server; the blocker is
  resolved. `fabrica.presidium.rest_client.RestPresidiumClient` -- real
  REST+mTLS, circuit-breaker protected, fail-closed, verified against
  `httpx.MockTransport` (24 tests) and a real end-to-end running Presidium
  server (real certs, not mocks). New `fabrica[presidium]` extra. Published
  to PyPI as `fabrica-context` v0.2.0, confirmed live via a real fresh-venv
  install. See `HANDOFF.md`'s own item 6 for the full writeup.
- [ ] Managed-provider adapters' real credentials (E2B/Modal/AWS/Azure/GCP)
  -- also deliberately deprioritized behind self-hosted Tier 2 regardless.

### Deliberately out of scope for this plan — already correctly deferred, revisit only if forced

Third-party skill trust/signing, log tamper-evidence, other memory backends
beyond Mem0, `SKILL.md` optional fields/bundled resources, Windows Tier 1,
macOS/Windows Tier 2 `vsock` equivalents (`VZVirtioSocketDevice`/`AF_HYPERV`).
Named in `problem-definition.md`/`HANDOFF.md` already — not re-litigated here.
