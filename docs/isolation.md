# Isolation: the tiered `Sandbox` protocol

**Status:** Design · **Last updated:** 2026-08

---

## Why this matters

Code mode runs **model-generated code**. That code is untrusted by definition. The
value of Fabrica's headline feature is only as real as the isolation underneath it.
Fabrica makes isolation a **pluggable, tiered `Sandbox` protocol**: cheap in dev,
hardware-grade in production, with no change to agent code.

## The tiers

| Tier | Backend | Isolation model | Cold start | Notes |
|---|---|---|---|---|
| 0 | subprocess / OS user | none → OS-level | ~0 | trusted code, local dev only |
| 1 | **gVisor** | user-space kernel (intercepts syscalls) | ~100 ms | strong-ish, cheap; used by Modal, Google Agent Sandbox |
| 2 | **Firecracker** | microVM, own kernel (KVM) | ~125 ms boot; **~4 ms restore from snapshot** | production target; used by AWS Lambda, E2B, Fly.io, Vercel Sandbox |
| 3 | **Kata Containers** | microVM inside Kubernetes | ~60–150 ms | k8s-native multi-tenant; can use Firecracker/Cloud Hypervisor as VMM |

Related options considered:
- **Cloud Hypervisor** — Rust VMM (~200 ms), often the VMM under Kata.
- **V8 isolates** (Cloudflare) — millisecond starts but JS-centric and weaker than a
  microVM; not a fit for arbitrary Python tool code.
- **Managed sandboxes** (E2B, Modal, Daytona) — buy-not-build option; Fabrica should
  ship adapters so teams can point at them instead of self-hosting Firecracker.

**Recommendation:** default **Tier 0** for dev, **Tier 1 (gVisor)** as the safe
multi-tenant default, **Tier 2 (Firecracker)** as the production target for untrusted
code. Offer managed-sandbox adapters (E2B/Modal) as a zero-ops path.

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
  (E2B reports sub-30 ms starts from snapshots).

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

1. Self-host Firecracker vs. default to a managed adapter (E2B/Modal) for v1?
2. GPU-in-sandbox (Modal-style) — needed for any Fabrica workloads, or out of scope?
3. Snapshot image supply chain — how are base images signed/verified (ties to
   Presidium/tool-poisoning concerns)?
4. Bare-metal requirement — Firecracker needs KVM; document the deploy constraints
   (nested virt on cloud VMs, or bare-metal nodes).
