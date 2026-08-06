# Spike: Firecracker boot + snapshot/restore latency (real hardware)

## Question

Does a minimal Firecracker microVM — booted bare, without jailer — hit boot
times and snapshot/restore latencies in the range cited in
[docs/isolation.md](../../docs/isolation.md) (~125ms boot, single-digit-ms
restore), when measured on real hardware rather than cited from research?

Previously blocked (no KVM-capable environment available). Unblocked via
SSH access to a homelab server (`kodiak@darkenergy`, Ubuntu 24.04, AMD-V,
bare metal — not nested virtualization).

## Result

**Answered.** Restore latency claim holds. Boot latency claim needs a
precise correction: it's true for VMM readiness, **not** for a full OS
reaching usable userspace.

## Findings

Real Firecracker v1.16.1, bare (no jailer — explicitly out of scope, flagged
up front), 2 vCPU / 512 MiB guest, official Ubuntu 24.04 CI kernel
(`vmlinux-6.1.177`) and rootfs, on bare-metal AMD-V hardware.

### Boot timing — one number hides two very different things

| Stage | Time |
|---|---:|
| Process spawn → API socket ready | 13.2ms |
| API configuration calls (boot-source, drive, machine-config) | 19.3ms |
| `InstanceStart` → VMM reports state=`Running` | **10.5ms** |
| `InstanceStart` → real userspace (login prompt / systemd target in console) | **1,055.5ms** |

The `~125ms boot` figure cited in `isolation.md` and the research behind it
almost certainly refers to something closer to the first number — VMM/kernel
handoff — not a full distro reaching an interactive login prompt. **A real,
unoptimized Ubuntu 24.04 rootfs with systemd took over 1 second to become
actually usable**, two orders of magnitude past "VMM=Running." Production
sandboxes (E2B, etc.) that hit ~125ms almost certainly use a minimal,
purpose-built rootfs/init, not a full distro image — this spike used the
"realistic but unoptimized" image on purpose, to surface exactly this gap
rather than construct a best-case demo.

### Snapshot creation — a real cost the cited figures don't mention

| Operation | Time |
|---|---:|
| Pause running VM | 8.4ms |
| **Create full snapshot** (512 MiB guest memory + state to disk) | **807ms** |

The commonly cited "single-digit ms" figures are about **restoring** from an
already-made snapshot, not **creating** one. Creating a snapshot is a real,
non-trivial cost (~800ms here, disk-write-bound at ~512MiB written) — it's a
one-time cost per warm-pool member, not a per-request cost, but it's absent
from every design doc so far and needs to be budgeted into pool
initialization/refresh planning.

### Restore — the headline claim, and it holds

Five independent trials, fresh process each time, loading the same snapshot:

| Trial | Restore time |
|---|---:|
| 1 (from the full boot/snapshot/restore run) | 10.7ms |
| 2 | 8.3ms |
| 3 | 8.1ms |
| 4 | 8.2ms |
| 5 | 8.4ms |

**Min 8.1ms, max 10.7ms, tight clustering around ~8.3ms.** This directly
validates the "single-digit ms restore" claim in `isolation.md` and closely
matches (arguably beats) E2B's own reported sub-30ms-from-snapshot figure.
Marcus's success metric in `problem-definition.md` ("p99 in single-digit ms
from a Firecracker snapshot") is **credible** based on this evidence — though
5 trials on one machine is a sample, not a rigorous p99.

## Evidence

Scripts: `specs/archive/spikes/scripts/spike-firecracker-boot-restore/`
(held, not deleted — see below for what's saved). Raw output:

```
=== Phase 1: boot timing ===
spawn->socket-ready:      .013231840s
configure API calls:      .019343585s
InstanceStart->VMM=Running: .010453840s
InstanceStart->userspace(login/target): 1.055512546s

=== Phase 2: snapshot / restore timing ===
pause took: .008419261s
snapshot create took: .807327894s

=== Phase 3: restore timing (fresh process) ===
RESTORE took: .010697668s
```

```
restore trial 1: .008327744s
restore trial 2: .008066627s
restore trial 3: .008175705s
restore trial 4: .008368564s
```

Setup notes (no `sudo` was actually required, contrary to the original
plan): Firecracker binaries are static, downloaded straight into `~/bin`.
The only genuinely privileged action across this whole spike was
`sudo usermod -aG kvm kodiak` (done by the user beforehand). Building the
ext4 rootfs from the CI-provided squashfs normally needs `sudo chown`/
`sudo mkfs.ext4` per Firecracker's own getting-started guide — worked around
with `fakeroot`, avoiding a second privileged request.

## Implications for the plan

- **Correct `isolation.md`'s boot-time claim.** State explicitly: ~10ms is
  VMM/kernel-handoff readiness; full OS userspace readiness with an
  unoptimized image is ~1s+. The "~125ms" figure needs either a citation
  caveat or a note that it assumes a minimal, purpose-built image — not "any
  Firecracker guest."
- **Warm pools are not optional — they're load-bearing for the whole
  latency story.** Cold boot-to-usable is far too slow for a request path;
  restore-from-snapshot (~8ms) is what makes the tiered `Sandbox` design
  viable at all. This was implied before; it's now measured, not assumed.
- **Snapshot creation cost needs a place in the design.** `isolation.md`
  should account for the ~800ms-per-member cost of *building* the warm pool,
  not just the cost of *drawing from* it.
- **A minimal purpose-built rootfs is a real, separate piece of work** if
  Fabrica wants boot-from-cold anywhere near ~125ms — not just "use
  Firecracker," but "build and maintain a tiny init/rootfs," which is its
  own scope, not previously called out.

## What was NOT explored

- **`jailer`** — explicitly out of scope this round, as flagged before
  starting. Its chroot/cgroup/seccomp setup adds unmeasured overhead.
- **Concurrent guests / pool behavior** — only ever one VM at a time.
  Contention when a real pool serves many agents wasn't tested.
- **A minimal custom rootfs** — this spike deliberately used a realistic,
  unoptimized full Ubuntu image to surface the boot-time gap; it did not
  build or measure a production-style minimal image, which is the natural
  next question.
- **Networking** — booted with no TAP device/network interface at all
  (not needed for timing); a real sandbox would need this wired up, with its
  own setup cost not measured here.
- **Rigorous p99** — 5 restore trials is a sample, not a statistically
  robust percentile.
- **Snapshot loading via `mem_backend: Uffd`** (lazy/on-demand memory
  loading) — `isolation.md` mentions UFFD lazy loading as a warm-pool
  mechanism; this spike used the simpler `File` backend, not UFFD.

## Recommendation

**Proceed with Firecracker as the Tier 2 backend — the core viability claim
(fast restore) is real, measured, and credible.** Before `plan-work`:

1. Correct the boot-time claim in `isolation.md` to distinguish VMM-ready
   from userspace-ready, and note the minimal-image assumption behind
   ~125ms figures.
2. Add snapshot-creation cost (~800ms/member) to the warm-pool design as a
   pool-initialization cost, not a request-path cost.
3. Scope "build a minimal purpose-built rootfs/init" as its own work item if
   cold-boot performance ever matters (e.g. pool exhaustion fallback) —
   don't assume the full-Ubuntu-image path gets anywhere close to ~125ms.
4. `jailer` overhead remains a real open question for a future spike.
