# Contract: `Sandbox` / `SandboxPool`

**Status:** Implemented -- `SubprocessSandbox` (Tier 0), `SrtSandbox`
(Tier 1, wraps `srt`), and `FirecrackerSandbox` (Tier 2, self-hosted) all
real, tested against real hardware (`SubprocessSandbox` continuously;
`SrtSandbox` live-verified on macOS, including real network-allowlist
enforcement -- Linux/Windows untested, `srt` documents support for both
but neither has been exercised here; `FirecrackerSandbox` against a real
homelab with KVM -- see
[SPIKE-firecracker-vsock-callback-bridge.md](../../specs/archive/spikes/SPIKE-firecracker-vsock-callback-bridge.md)).
`SubprocessSandbox` and `SrtSandbox` share their subprocess-launch/ZMQ
mechanics via `_shim_runner.run_shimmed_subprocess()`, parameterized only
by the command prefix wrapping the guest shim -- not two independently
maintained copies. Tier 1 gVisor (Linux) remains not implemented
separately; `srt`'s own Linux support (bubblewrap+netns) may cover that
platform once verified, avoiding a second Tier 1 implementation
entirely. · **Last updated:** 2026-08
**Supersedes:** the `Sandbox` sketch in [isolation.md](../isolation.md)
**Depends on:** [system-design.md](../system-design.md) §1 (object model), §3
(internal code-mode flow, the callback this contract implements), §6 (resilience
decisions this contract makes concrete)
**Corrects:** `system-design.md`'s open-question-1 resolution — see below.

---

## A correction found by writing this out precisely

`system-design.md §6`/§7 describes a cold-started overflow sandbox as being
*"folded back into the warm pool... rather than discarded."* That's imprecise in
a way that matters: **the instance itself is never reused.** Arbitrary
model-generated code just ran inside it — it may have written files, mutated
memory, left arbitrary state. Reusing that *same live instance* for a different
task or agent would leak state across an isolation boundary this whole design
exists to enforce.

What actually happens: every used instance is **always terminated** after
`run()`. The pool "regrows" by restoring a **fresh** instance from the clean base
snapshot — not by recycling the dirty one. The externally-visible behavior
(pool size trends back toward `warm_size` after a burst) is the same; the
mechanism is different, and the difference matters for correctness, not just
precision. This contract implements the corrected version; `system-design.md`
should be updated to match (noted at the end of this doc, not silently left
inconsistent).

---

## Two levels, matching the `Retriever`/`RetrieverBackend` split

`Sandbox` is the narrow, swappable per-tier backend. `SandboxPool` is the one
public class `ToolManager`/`SkillManager` actually depend on — it owns warm-pool
bookkeeping, the bounded-overflow behavior, and the always-clean-instance
guarantee, in one place, not duplicated per backend.

```python
class Sandbox(Protocol):
    """A single-tier backend. Implementations: SubprocessSandbox (Tier 0),
    GvisorSandbox / SrtSandbox (Tier 1), FirecrackerSandbox / LibkrunSandbox
    (Tier 2). Never used directly outside Fabrica — always wrapped by
    SandboxPool."""

    async def boot_clean(self) -> SandboxHandle:
        """Boot, or restore-from-snapshot, a fresh instance in a known
        clean state. This is the ONLY way an instance is created — there
        is no reuse path in this Protocol at all."""
        ...

    async def execute(
        self,
        handle: SandboxHandle,
        code: str,
        *,
        on_tool_call: ToolCallCallback,
        timeout: float,
    ) -> RunResult: ...

    async def terminate(self, handle: SandboxHandle) -> None:
        """Tear down an instance permanently. Called by SandboxPool on
        every release() — never on a path that reuses the handle."""
        ...

    async def health_check(self) -> bool: ...
```

```python
class SandboxPool:
    """The public engine. ToolManager and SkillManager depend on this,
    never on a Sandbox backend directly."""

    def __init__(
        self,
        backend: Sandbox,
        *,
        warm_size: int,
        max_concurrent: int,
        acquire_timeout: float = 5.0,
    ) -> None:
        """`backend` is resolved by CivitasBridge at construction time
        based on host OS + deployment tier (isolation.md's platform
        dispatch) — never chosen per-call. `warm_size` and
        `max_concurrent` implement the bounded-overflow design from
        system-design.md §6/§7."""
        ...
```

---

## Types

```python
@dataclass(frozen=True)
class SandboxHandle:
    """An opaque reference to one running instance. Callers must not
    depend on its internal fields — they vary by backend/tier."""
    id: str
    tier: int


ToolCallCallback = Callable[[str, dict], Awaitable[dict]]
"""Invoked once per namespace.call(tool, params) the running code makes.
Delivered via direct ZMQ ipc:// for Tier 0/1, or the guest-side relay
bridging to vsock / VZVirtioSocketDevice / AF_HYPERV for Tier 2 —
system-design.md §3. The caller (ToolManager) supplies this; it is where
the actual tool execution and grant-checking happens, not inside Sandbox
itself."""


@dataclass(frozen=True)
class RunResult:
    """The only thing that crosses back out of the sandbox boundary.

    Deliberately stdout-based, not a magic "return value" mechanism —
    this matches exactly what SPIKE-code-mode-execution.md validated
    (the model's generated code prints its answer; that's what comes
    back), not an idealized structured-value extraction that was never
    tested."""

    success: bool
    stdout: str                 # capped — see MAX_STDOUT_BYTES below
    stdout_truncated: bool
    error_message: str | None   # populated iff success is False; the
                                 # CODE's own exception/traceback — this
                                 # is a routine outcome, not a raised
                                 # SandboxError (see Errors, below)
    cpu_seconds: float
    duration_ms: float
    tool_call_count: int        # matches the OTEL span attribute,
                                 # system-design.md §7


MAX_STDOUT_BYTES = 65536   # 64KB. Exceeding this truncates, sets
                           # stdout_truncated=True — never silently
                           # drops data without signaling it happened,
                           # never raises for a chatty print() loop.
```

---

## Errors

```python
class SandboxError(Exception):
    """Base for all Sandbox/SandboxPool errors."""


class SandboxTimeoutError(SandboxError):
    """Code did not complete within the given timeout. The instance is
    killed; the handle is no longer usable after this — callers must not
    run() with the same handle."""


class SandboxCrashedError(SandboxError):
    """The instance died unexpectedly during run() — not a timeout, not
    a code-level exception. Handle is no longer usable. Matches
    system-design.md §6's "Sandbox crashes mid-run" row: SandboxPool
    discards the handle; Civitas supervision (a separate concern) is
    what actually restarts the underlying supervised process, if this
    pool is running in service mode."""


class SandboxPoolExhaustedError(SandboxError):
    """acquire() found no handle within acquire_timeout — both the warm
    pool and cold-start-up-to-max_concurrent were unavailable. Structured
    error, not a hang, per system-design.md §6's resolution of this
    exact failure mode."""
```

**Why code-level failures are *not* exceptions:** if the generated code raises,
that's a routine, expected outcome the model may legitimately need to see and
correct — `RunResult(success=False, error_message=...)`, not a raised Python
exception propagating through `ToolManager`. Exceptions are reserved for
infrastructure failures (timeout, crash, exhaustion) that are genuinely
exceptional, not "the model wrote code with a bug."

---

## Methods

```python
async def acquire(self) -> SandboxHandle:
    """Get a handle for this deployment's configured tier — never a
    per-call choice (isolation.md's platform-dispatch principle: the
    backend is hidden from callers, not just from end users).

    Tries the warm pool first (fast path — Firecracker restore measured
    at 8–11ms in SPIKE-firecracker-boot-restore-latency.md). If empty
    and current concurrent count is under max_concurrent, boots a fresh
    instance on demand (bounded, accepting the cold-boot cost measured
    in the same spike). If at max_concurrent, queues up to
    acquire_timeout.

    Raises:
        SandboxPoolExhaustedError: no handle became available in time.
    """

async def run(
    self,
    handle: SandboxHandle,
    code: str,
    *,
    on_tool_call: ToolCallCallback,
    timeout: float = 30.0,
) -> RunResult:
    """Execute code inside the instance referenced by handle. Every real
    tool invocation the code makes calls on_tool_call via the
    tier-appropriate channel; intermediate results never leave the
    sandbox boundary — only the final RunResult does (this is the
    mechanism behind the token-savings measured in
    SPIKE-code-mode-execution.md, not a diagram simplification).

    After this call returns OR raises, the handle must be passed to
    release() — run() does not release it implicitly, since a caller
    may want to inspect handle-adjacent state (not exposed by this
    Protocol, but reserved) before releasing.

    Raises:
        SandboxTimeoutError, SandboxCrashedError: handle is no longer
            usable after either.
    """

async def release(self, handle: SandboxHandle) -> None:
    """Return a handle after use. The underlying instance is ALWAYS
    terminated — never reused live, regardless of whether run()
    succeeded, failed, or was never called at all after acquire().

    If the pool is under warm_size after this termination, triggers a
    background boot_clean() to restore a FRESH instance from the base
    snapshot and refill the warm slot. This is the corrected version of
    system-design.md §6's "regrow the pool" language — it is a fresh
    restore, not a reuse of the just-released instance."""

async def close(self) -> None:
    """Real gap found by testing SandboxPool wrapped around a REAL
    backend (FirecrackerSandbox) rather than only the fast in-memory
    _FakeBackend used to test the pool's own bookkeeping in isolation:
    nothing terminates the warm pool's resident instances at shutdown.
    With SubprocessSandbox this was a real but easy-to-miss leak (an
    orphaned OS process); with FirecrackerSandbox it is a full,
    impossible-to-miss orphaned rootfs copy per warm slot, found by
    inspecting /tmp after real test runs.

    Must be called once, at deployment shutdown, by whichever owner
    constructed this pool (CivitasBridge.build()'s caller, or a test's
    own teardown). Waits for any in-flight background refill task
    (release()'s fire-and-forget boot_clean(), see above) to finish
    FIRST — otherwise a refill that completes after draining would add
    one more never-terminated instance to an already-emptied warm
    list — then terminates every handle still resident in the warm
    pool and clears it. Safe to call on an already-closed pool (a
    second call terminates nothing, since the warm pool is already
    empty and there are no pending refill tasks left to wait for)."""
```

---

## What this contract deliberately does not cover

- **Image-baking** (the tool-namespace shim, the Tier-2 ZMQ relay) is
  `SandboxPool`'s construction-time responsibility per `system-design.md §4`,
  not something `acquire`/`run`/`release` expose — a caller never sees or
  configures image contents through this contract.
- **The `vsock`/`VZVirtioSocketDevice`/`AF_HYPERV` bridge implementation** is
  internal to each Tier-2 `Sandbox` backend. This contract specifies that
  `on_tool_call` gets invoked correctly regardless of tier — not how each
  backend's relay achieves that.
- **Presidium's grant check** happens in `ToolManager`, before `acquire()` is
  even called (`system-design.md §3`, step 4) — not inside this contract.

## Real addition: a queryable `tier` property, closing `contracts/mcp-server.md`'s open `WeakIsolationError` gap

Neither `Sandbox` nor `SandboxPool` originally exposed which isolation tier
a backend actually provides -- `contracts/mcp-server.md`'s
`WeakIsolationError` needed exactly this and had nothing to check against,
so its check was accepted-but-inert. Closed by adding a read-only
`tier: int` property to both:

```python
class Sandbox(Protocol):
    @property
    def tier(self) -> int:
        """0/1/2 -- isolation.md's capability levels. Fixed per backend
        instance, never changes at runtime."""
        ...
    # ... boot_clean/execute/terminate/health_check unchanged
```

`SandboxPool.tier` delegates straight to `self._backend.tier` -- `SandboxPool`
never chooses or changes tier itself, only `CivitasBridge`'s platform
dispatch does, once, at construction. `ToolManager.tier`/`SkillManager.tier`
(`contracts/managers.md`) delegate the same way one level up, so
`FabricaMCPServer` can check isolation strength (`fabrica.tools.tier`)
without reaching into either manager's private `SandboxPool` reference.

A plain `int`, not an enum -- the only thing ever done with it is a `< 2`
comparison; an enum would add ceremony with no behavior this doesn't
already have.

**Honest consequence, stated directly rather than glossed over**: only
`SubprocessSandbox` (Tier 0) is actually implemented anywhere in this
codebase today. This means, as of this writing, every real
`FabricaMCPServer(kind="http")` deployment must pass
`allow_weak_isolation_for_external_callers=True` to construct at all --
that is the fail-closed default working exactly as intended (see
`contracts/mcp-server.md`'s own note on this), not a bug to work around.

## `FirecrackerSandbox` -- real Tier 2 implementation notes

Implements this exact `Sandbox` Protocol (`src/fabrica/sandbox/firecracker_backend.py`),
validated end to end on real hardware (real `vsock`, a real tool call
crossing the VM boundary, a real result returning --
[SPIKE-firecracker-vsock-callback-bridge.md](../../specs/archive/spikes/SPIKE-firecracker-vsock-callback-bridge.md)).

**v1 scope, decided deliberately**: `boot_clean()` always cold-boots --
it does not restore from a snapshot yet. `boot_clean()`'s own docstring
already allows this ("boot, OR restore-from-snapshot"), so this isn't a
contract violation, but it's a real, named limitation: cold boot to real
userspace readiness measured ~1,055ms in
[SPIKE-firecracker-boot-restore-latency.md](../../specs/archive/spikes/SPIKE-firecracker-boot-restore-latency.md),
far slower than restore's ~8-10ms. Snapshot/restore combined WITH the
vsock callback bridge is a genuinely separate, unvalidated combination --
neither spike tested restoring a snapshot of a guest with an already-live
vsock connection. Deferred as real, focused follow-on work, not built
speculatively before a correct cold-boot v1 exists.

**Requires a pre-built rootfs with the guest shim already baked in** --
`FirecrackerSandbox` does not build this itself; `kernel_image_path`/
`base_rootfs_path` are real, deployment-specific artifacts it receives
fully-constructed, the same DI shape used for every other injected
dependency in this project (`Summarizer`, `PresidiumClient`). The actual
baking procedure (mount the rootfs, copy `_firecracker_guest_shim.py` in)
was first validated by hand, step by step, in the spike above -- now a
real, reusable, documented deliverable instead:
[`scripts/build_firecracker_rootfs.sh`](../../scripts/build_firecracker_rootfs.sh),
documented end to end (including the exact, precisely scoped `sudo`
rules needed -- `mount`/`umount`/`losetup` against one fixed mount
point, plus a `cp *.py`-scoped rule, not blanket root access) in
[deployment/firecracker-rootfs.md](../deployment/firecracker-rootfs.md).
This is what makes a SECOND deployer able to actually produce a working
image, not just re-read a spike's transcript of one machine's history.

**Two real resource leaks found and fixed by inspecting the filesystem
after real test runs, not assumed clean**: `terminate()` was cleaning up
the `{vsock_uds}_{port}` guest-connection proxy socket but not
`vsock_uds` itself (Firecracker's own separate vsock control path); and
`boot_clean()`'s own failure paths (a guest that never sends "ready",
timeout, unexpected first message) weren't cleaning up ANY of the files
they'd created before raising -- only the success path was. Both fixed
with one shared `_cleanup_files()` helper used everywhere an instance is
torn down, successfully or not. A dedicated regression test
(`test_terminate_removes_every_file_it_created`) asserts this directly by
checking `/tmp` for leftovers after `terminate()`, not just that
`terminate()` runs without raising.

**`cpu_seconds` is `0.0`, honestly, not silently wrong** -- real
per-VM CPU accounting needs Firecracker's own metrics API, not wired up
in this v1 pass. Stated as a real, known gap rather than a plausible-
looking but fabricated number.

## Real addition: `tracer` DI and `trace_id`/`parent_span_id` on `acquire()`/`run()`

`SandboxPool` now accepts an optional `tracer: fabrica.observability
.Tracer | None = None` (defaults to `NullTracer()`, same DI shape as
everywhere else). `acquire()`/`run()` both gained optional `trace_id`/
`parent_span_id` keyword parameters -- default to "start a fresh root
span", so a direct caller never has to think about tracing to use these
correctly, but `execute_in_sandbox` passes its own outer span's identity
down so `fabrica.sandbox.acquire`/`fabrica.sandbox.run` nest as real
children, not disconnected spans. Full design:
[system-design.md §7](../system-design.md#7-observability-spans-this-system-emits).

## Open items for implementation

1. ~~`boot_clean()`'s background-replenishment trigger... not decided
   here.~~ **Resolved during implementation**: fire-and-forget, tracked
   in a `set` of tasks specifically so `SandboxPool.close()` can wait
   for any in-flight one before draining the warm pool (see `close()`
   above and its own real-gap note).
2. ~~What happens if `release()` is called with a handle that was never
   `acquire()`'d, or already released?~~ **Resolved, consistent with
   `Retriever.deregister()`'s precedent as guessed here**: both real
   backends treat an unknown/already-terminated handle id as a no-op,
   not an error -- `FirecrackerSandbox.terminate()` does
   `self._instances.pop(handle.id, None)` and returns early;
   `SubprocessSandbox.terminate()`'s `unlink(missing_ok=True)` is
   harmless regardless. Found true by inspection, not newly added.
3. ~~Whether `run()`'s `on_tool_call` callback itself needs its own
   timeout...~~ **Resolved: yes.** New optional `tool_call_timeout: float
   | None = None` on `Sandbox.execute()`/`SandboxPool.run()`, threaded
   through `execute_in_sandbox`/`ToolManager.run_code()`/`SkillManager
   .run()`. `None` (the default) preserves the original behavior exactly.
   When set, a hung tool call raises the new `SandboxToolCallTimeoutError`
   (a `SandboxTimeoutError` subclass, same consequence -- instance killed,
   handle unusable -- just attributed specifically) well before the much
   larger overall `timeout` would have caught it, on both backends.

   **A real, subtle bug found and fixed while implementing this on
   `SubprocessSandbox`, not assumed correct**: the original
   `asyncio.wait_for(proc.communicate(...), timeout=timeout)` structure
   ran `serve_task` (the tool-call loop) as a fully separate fire-and-
   forget task -- a hung/timed-out tool call inside it was never actually
   observed by `execute()` at all, silently consuming the FULL overall
   `timeout` budget before a generic, unattributed `SandboxTimeoutError`
   finally fired. Fixed by racing both tasks with
   `asyncio.wait(..., return_when=FIRST_COMPLETED)`. That fix itself
   surfaced a second, genuinely nasty bug: cleanup code in `finally`
   awaited the already-exception-holding `serve_task` while only
   suppressing `asyncio.CancelledError`, not the actual exception it
   held -- silently re-raising the SAME exception type a second time,
   which skipped every remaining cleanup statement (`ctx.term()`
   included) without ever surfacing as a visible test failure, because
   the re-raised exception happened to match what the calling test's
   `pytest.raises()` was already expecting. The leaked `zmq.asyncio
   .Context` then hung indefinitely during a full garbage-collection
   pass at process shutdown (confirmed via a real stack dump, not
   guessed) -- entirely invisible unless a test explicitly checks the
   process exits cleanly, not just that assertions pass. Fixed by
   suppressing any exception (not just `CancelledError`) when draining
   an already-completed task purely for cleanup purposes.
4. **New**: `FirecrackerSandbox`'s snapshot/restore support -- v1 always
   cold-boots; validating restore combined with an already-live vsock
   connection, then implementing it, is real, separate follow-on work.
5. **New**: `FirecrackerSandbox` uses the existing, general-purpose Ubuntu
   24.04 rootfs image (with the guest shim copied in) -- a real, minimal,
   purpose-built rootfs/init (named as its own scope item since the very
   first Firecracker spike) is still not built.
6. **New**: `jailer` (cgroups/namespaces/seccomp/chroot hardening) remains
   completely unexplored -- explicitly out of scope in both Firecracker
   spikes, a real gap for production-grade defense-in-depth, not resolved
   here.
7. **New**: `FirecrackerSandbox`'s real CPU-second accounting
   (`cpu_seconds` is currently always `0.0`) needs Firecracker's own
   metrics API wired up -- not done in this v1 pass.

## Correction applied to `system-design.md`

§6/§7's "cold-started overflow sandbox... folded back into the warm pool
rather than discarded" language has been corrected there to match this
contract's `release()` semantics (fresh restore, not reuse) — see the note in
[system-design.md §6](../system-design.md), which credits this contract as the
source of the correction rather than silently fixing it in both places at once.
