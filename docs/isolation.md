# Isolation: the tiered `Sandbox` protocol

**Status:** Design · **Last updated:** 2026-08

---

## Why this matters

Code mode runs **model-generated code**. That code is untrusted by definition. The
value of Fabrica's headline feature — now validated, see
[SPIKE-code-mode-execution.md](../specs/archive/spikes/SPIKE-code-mode-execution.md)
— is only as real as the isolation underneath it. Fabrica makes isolation a
**pluggable, tiered `Sandbox` protocol**: cheap in dev, hardware-grade in
production, with no change to agent code — and, per direct product feedback,
**no change to agent code across platforms either.** Users don't care whether
Firecracker, `srt`, or libkrun is underneath; they care that the problem (safe
execution) is solved on whatever OS they're on. See platform dispatch, below.

## Platform dispatch — auto-detected, not user-configured

Unlike transport (an explicit user choice in Civitas topology config —
`type: nats` vs `type: in_process`), **the isolation backend is not a normal
user-facing knob.** The `Sandbox` factory auto-detects the host OS at startup and
selects the tier-appropriate backend internally. This is a deliberate exception to
the platform's usual "expose it in config" pattern: isolation backend choice is an
implementation detail Fabrica manages on the user's behalf, not a deployment
topology decision the way transport is. A hidden override exists for testing/CI
only — it is not documented as a supported user option.

**Implemented**: `fabrica.sandbox.select_sandbox_backend()` is the real
auto-detection function `CivitasBridge.build()` calls by default. Real
host detection today has three outcomes, stated honestly rather than
matching the full five-tier table below in the abstract: Firecracker
(Tier 2) when Linux + real `/dev/kvm` + real
`FABRICA_FC_BINARY`/`FABRICA_FC_KERNEL`/`FABRICA_FC_ROOTFS` artifacts are
all present; `SrtSandbox` (Tier 1) when the real `srt` binary is on PATH
and Firecracker wasn't selected; `SubprocessSandbox` (Tier 0) otherwise.
The hidden testing/CI override is `CivitasBridge`'s own `sandbox_backend`
constructor parameter — real deployments never pass it.

**`SrtSandbox` note, added when this was implemented**: wraps `srt`
(`@anthropic-ai/sandbox-runtime`) around the exact same guest-shim/ZMQ
`ipc://` mechanism `SubprocessSandbox` already uses (shared in
`_shim_runner.py`) — the OS-level restriction wraps the outside of an
identical subprocess invocation, not a different execution model. Real,
OS-enforced, allow-only network policy (`network.allowedDomains`, empty
by default = deny all) closes a confirmed gap in Tier 0: its guest shim
execs code with unrestricted Python builtins, so sandboxed code can
`import socket` directly, bypassing the intended tool-call bridge
entirely. Verified live on macOS (Seatbelt): an allowlisted domain
resolves and connects, a non-allowlisted one gets a real proxy-level
403. `srt` also documents Linux (bubblewrap+netns) and Windows (WFP)
support — **not yet exercised on either**, stated honestly rather than
assumed from the README. One asymmetry worth knowing: `srt`'s
`allowUnixSockets` allowlist is path-based on macOS only ("ignored on
Linux, seccomp cannot filter by path" per its own schema) — the guest
shim's callback socket needs either a per-path allow (macOS) or the
broader `allowAllUnixSockets` opt-in (Linux, a real, small isolation
weakening: any Unix socket, not just this one), never silently
defaulted.

## The tiers, per platform

Each tier is a **capability level**, not a single technology — what actually backs
it depends on the host OS, auto-detected and swapped in transparently.

| Tier | Linux | macOS | Windows |
|---|---|---|---|
| 0 | subprocess / OS user — none → OS-level, ~0ms | *(same)* | *(same)* |
| 1 | **gVisor** — user-space kernel, ~100ms — not implemented; `srt` (below) may cover this platform instead, since it also supports Linux via bubblewrap+netns, not yet exercised here | **`SrtSandbox` — implemented** (`contracts/sandbox.md`), wraps `srt` (Anthropic's Sandbox Runtime, `sandbox-exec`/Seatbelt) — real enforcement confirmed (write/read/network denial, allow-only network via `network.allowedDomains`), live-verified end to end including the guest-shim ZMQ callback bridge, **p50 152ms** ([SPIKE-macos-isolation-srt-libkrun.md](../specs/archive/spikes/SPIKE-macos-isolation-srt-libkrun.md)) | `srt` claims Windows support (`windows-install`: dedicated `srt-sandbox` user + WFP filters) via the same `SrtSandbox` implementation — **untested, deferred**. Windows is a small segment; if `srt` works there, no further action needed. If a real gap surfaces, spike then. |
| 2 | **Firecracker** — microVM, own kernel (KVM). **Implemented** (`FirecrackerSandbox`, `contracts/sandbox.md`) -- real, cold-boot-only v1, validated end to end on real hardware including a real `vsock` tool-call round trip ([SPIKE-firecracker-vsock-callback-bridge.md](../specs/archive/spikes/SPIKE-firecracker-vsock-callback-bridge.md)). Boot: **VMM-ready ~10.5ms**, **full-userspace-ready ~1,055ms with an unoptimized image** — these are different signals, not one number ([SPIKE-firecracker-boot-restore-latency.md](../specs/archive/spikes/SPIKE-firecracker-boot-restore-latency.md)). **Restore from snapshot: 8.1–10.7ms**, validated separately -- now combined with the vsock bridge too (`SPIKE-firecracker-snapshot-restore-vsock-combination.md`): a real spike found and fixed two real gaps (a stale vsock socket file `SIGKILL` never cleans up; the guest kernel-panicking on resume because the shim has no reconnect logic for a real, correct `ConnectionResetError`) -- the combination is real and buildable, verified end to end with a throwaway patched shim, but the fix isn't ported into the shipped guest shim yet, so v1 still always cold-boots (real implementation: `PLAN.md` item 20a). **Network isolation: no interface attached at all** (not a filtered one -- no `/network-interfaces` API call is ever made), verified on real hardware: a raw socket connect fails immediately with `ENETUNREACH`, DNS resolution fails immediately too -- structurally stronger than a policy-based allowlist, since there is no interface to misconfigure. | **libkrun** (`Virtualization.framework`-based) — **cold-boot-only, permanently: no snapshot/restore support exists.** This is a structural ceiling, not a bug to fix. Accepted and shippable — snapshot/restore is valuable, not mandatory. | Hyper-V isolation / Windows Sandbox — real, hypervisor-backed, but seconds-to-minutes boot, not milliseconds. Not a near-term priority. |
| 3 | **Kata Containers** — microVM in k8s, ~60–150ms | no direct equivalent (k8s-node-specific) | no direct equivalent |

Related options considered (Linux-specific unless noted):
- **Cloud Hypervisor** — Rust VMM (~200 ms), often the VMM under Kata.
- **V8 isolates** (Cloudflare) — millisecond starts but JS-centric and weaker than a
  microVM; not a fit for arbitrary Python tool code.
- **Managed sandboxes** (E2B, Modal, Daytona) — buy-not-build option; Fabrica should
  ship adapters so teams can point at them instead of self-hosting Firecracker.
- **Apple's Containerization framework** (macOS) — an alternative to libkrun for
  Tier 2, described as similar in isolation strength to Kata. **Untested** — not
  chosen over libkrun, just not yet evaluated.

**Recommendation:** default **Tier 0** for dev on any platform. **Tier 1** as the
safe multi-tenant default — gVisor (Linux) or `srt` (macOS), auto-selected. **Tier
2** as the long-term production target for untrusted code on Linux (self-hosted
Firecracker, warm-pool restore validated fast); on macOS, Tier 2 is available but
honestly weaker (no warm pool) — document the ceiling, don't hide it.

**Build sequencing, resolved (open question 1):** a managed-sandbox adapter
(E2B/Modal) ships FIRST, as Tier 2's initial real implementation on any platform
— not just a fallback path alongside a self-hosted Firecracker built in parallel.
Self-hosted Firecracker remains the stated long-term production target and is not
abandoned, just not the first thing built, given the effort asymmetry between the
two (see open question 1's full reasoning).

## The protocol

```python
class Sandbox(Protocol):
    """An isolated environment to run untrusted code with a bound ToolNamespace."""
    async def start(self, image: SandboxImage, limits: ResourceLimits) -> SandboxHandle: ...
    async def run(self, handle: SandboxHandle, code: str, *, timeout: float) -> RunResult: ...
    async def snapshot(self, handle: SandboxHandle) -> SnapshotRef: ...   # warm pools
    async def restore(self, snap: SnapshotRef) -> SandboxHandle: ...
    async def stop(self, handle: SandboxHandle) -> None: ...
    async def health_check(self) -> bool: ...
```

`RunResult` returns only stdout/return-value/errors and resource accounting — the
large intermediate data stays inside the sandbox, keyed by a handle the next `run`
can reference (this is how code mode reaches ~99% token reduction).

## Firecracker: the production backend

Architecture Fabrica must orchestrate (self-hosted Tier 2):

- **`jailer`** — wraps every Firecracker process in cgroups + namespaces + seccomp +
  chroot *before* boot. Always used for untrusted code (defence-in-depth).
- **REST API over a Unix socket** — control plane to configure vCPU/memory, block
  devices, network, and lifecycle. Off the VM fast path.
- **`vsock`** — host↔guest communication over `AF_VSOCK`↔`AF_UNIX`; how Fabrica ships
  code in and results out without exposing IP networking.
- **rootfs + kernel** — an `ext4` rootfs (convertible from a container image) plus an
  uncompressed `vmlinux`. Fabrica bakes a base image with the Python runtime + the
  tool-namespace shim.
- **Snapshot / restore + UFFD lazy loading** — serialize a booted-and-warmed microVM
  once, then fork many from it in single-digit ms. This is the warm-pool mechanism
  (E2B reports sub-30 ms starts from snapshots; measured on real hardware here at
  8.1–10.7ms — see the Firecracker spike). **Snapshot creation is a separate,
  non-trivial cost** — measured at ~807ms for a 512MiB guest — paid once per
  warm-pool member at pool-build time, not per request. Budget for it explicitly;
  it is invisible if you only think about restore latency.
- **A minimal, purpose-built rootfs now exists** (`scripts/build_firecracker_minimal_base.sh`,
  `docs/deployment/firecracker-rootfs.md`) — Ubuntu 24.04 + `python3` only,
  no systemd, built via a real Docker export + `mke2fs -d`. **Real result,
  stated precisely, not oversold**: this is a genuine disk-footprint and
  per-instance-copy win (apparent size 1.0G → 300M; the per-boot rootfs
  copy dropped ~945ms → ~265ms; end-to-end `boot_clean()` ~1910ms → ~1205ms,
  a real ~37% reduction), but guest kernel BOOT TIME itself barely moved —
  a full Ubuntu 24.04 + systemd image took ~1,055ms to reach usable
  userspace (original spike measurement); this minimal image's own
  full-boot figure lands in the same ~1.1–1.2s range. The commonly-cited
  "~125ms boot" figures assume something more than a smaller rootfs —
  most likely eliminating a full CPython interpreter's own startup cost
  as PID 1 entirely (a compiled/static init instead of `init=/usr/bin
  /python3`), which is real, uncounted work still not done here, not
  resolved by this rootfs alone.

Orchestration reference points:
- **E2B**: Firecracker + Nomad/Consul control plane, locally cached templates.
- **Fly.io**: `flyd` orchestrator + `containerd` to build rootfs from container images.

Fabrica's job is the **pool orchestrator**: bake/version images, keep a warm snapshot
pool, schedule runs, re-seed entropy/UUIDs on restore, enforce rate limiters, and
expose all of it behind the `Sandbox` protocol as a supervised Civitas `GenServer`.

## Integration with the platform

- **Civitas** supervises the sandbox pool `GenServer`; a crashed pool restarts under a
  supervisor; every run emits an OTEL span.
- **Presidium** governs: policy decides whether a code-mode run is permitted; grants
  scope the `ToolNamespace` bound into the sandbox; the credential path injects secrets
  (see tessera) so code can *use* them without them entering the model context.
- **Fabrica** owns the mechanics of isolation and pooling only.

## Open questions

1. ~~Self-host Firecracker vs. default to a managed adapter (E2B/Modal)
   for v1?~~ **Resolved -- both, sequenced -- and RE-SEQUENCED again after
   direct discussion, reversing the sequencing decision below.** This was
   never actually an either/or at the positioning level: both
   `isolation.md` and `landscape.md` already stated the same
   recommendation ("Tier 2 as the production target... offer
   managed-sandbox adapters as a zero-ops path"), and
   `problem-definition.md`'s Marcus non-goal ("no hosted managed-sandbox
   SaaS") is about Fabrica never *operating* a competing service, not
   about whether a Fabrica deployment may *delegate to* a managed
   provider -- fully compatible with the "wrap, don't build" thesis
   applied everywhere else in this project.

   ~~**Decided: build the managed adapter first.**~~ **RE-DECIDED: build
   self-hosted Firecracker first instead**, after a direct decision that
   the cost dimension is the deciding factor, not the effort asymmetry
   this doc originally weighed: **self-hosted Firecracker is genuinely
   free per-execution** (real hardware, or amortized infrastructure cost)
   — every one of the five researched managed providers bills per API
   call (`contracts/managed-sandbox.md`'s own "cost dimension" section).
   Given a real, live homelab already available for this
   (`kodiak@darkenergy` — Firecracker/`jailer` binaries, kernel, rootfs,
   and even a prior snapshot already sitting there from
   `SPIKE-firecracker-boot-restore-latency.md`, confirmed still live),
   the effort-asymmetry argument that justified building the cheaper
   managed adapter first no longer dominates — a real, free environment
   to build the harder thing in changes the calculus. **Managed
   providers get their interfaces designed now** (`ManagedSandboxAdapter`,
   `contracts/managed-sandbox.md`, already done) **but implementation
   is explicitly second priority**, picked up once self-hosted
   Firecracker is real.

   ~~Self-hosted Firecracker is now the actual next priority~~ **Done --
   `FirecrackerSandbox` is real** (`contracts/sandbox.md`), validated end
   to end on real hardware: real cold boot, a real `vsock` callback
   bridge with a real tool call crossing the VM boundary, real cleanup
   with no leftover resources (two real leaks found and fixed by
   inspecting the filesystem after real test runs -- see the contract's
   own implementation notes). v1 is cold-boot-only (no snapshot/restore
   yet -- a real, deliberately deferred combination, not yet validated
   together with the vsock bridge). **A real, minimal, purpose-built base
   rootfs now exists too** (`scripts/build_firecracker_minimal_base.sh`)
   -- a genuine disk-footprint/per-instance-copy win (~3.5x smaller,
   ~37% faster end-to-end `boot_clean()`), though guest boot time itself
   didn't meaningfully improve (see this doc's own boot-time section
   above for the honest, precise numbers). Now that self-hosted Tier 2 is real, managed-provider
   implementation becomes the next thing to pick up, per the sequencing
   decided above.

   Which managed provider ships once that phase starts — a real,
   researched field of five (E2B, Modal, AWS Bedrock AgentCore, Azure
   Container Apps Dynamic Sessions, GCP Agent Sandbox; see
   [landscape.md §3a](landscape.md)) — and whether real API credentials
   are available to build/test one for real (the same kind of gap
   `PresidiumClient`'s REST client hit), is tracked separately in
   `HANDOFF.md`, deliberately not blocking the self-hosted work.
2. GPU-in-sandbox (Modal-style) — needed for any Fabrica workloads, or out of scope?
3. Snapshot image supply chain — how are base images signed/verified (ties to
   Presidium/tool-poisoning concerns)?
4. ~~Bare-metal requirement~~ **Resolved by spike**: Firecracker needs KVM;
   validated end-to-end on real bare-metal AMD-V hardware
   ([SPIKE-firecracker-boot-restore-latency.md](../specs/archive/spikes/SPIKE-firecracker-boot-restore-latency.md)).
   Nested-virt-on-cloud-VM path remains untested.
5. **Apple's Containerization framework vs. libkrun for macOS Tier 2** — named as an
   alternative above, not evaluated. Worth a spike if libkrun's packaging friction
   (three separate config issues surfaced in
   [SPIKE-macos-isolation-srt-libkrun.md](../specs/archive/spikes/SPIKE-macos-isolation-srt-libkrun.md))
   becomes a real integration cost.
6. `srt`'s `--control-fd` persistent-mode — could improve the measured 152ms macOS
   Tier-1 latency the same way a persistent process helped prx (per
   [SPIKE-prx-invocation-latency.md](../specs/archive/spikes/SPIKE-prx-invocation-latency.md)).
   Deferred to implementation, not a design blocker.
7. `srt`'s Windows mode (`windows-install`) — claims to close the Windows Tier-1 gap,
   unverified. Deferred: Windows is a small segment; test if/when a real gap forces
   it, not preemptively.
