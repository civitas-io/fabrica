# Spike: is ZMQ viable as the sandbox callback wire protocol?

## Question

Is `libzmq`/`pyzmq` viable inside a minimal Linux guest image without pulling in
dependencies that fight the minimal-rootfs goal, and does the `ipc://` transport
actually work for the Tier 0/1 case (the design resolved in
[system-design.md §3](../../../docs/system-design.md))?

Sanity-check spike, explicitly scoped narrower than full verification: tests
package footprint and basic `ipc://` functionality on the same Ubuntu 24.04
x86_64 environment the earlier Firecracker spike's rootfs was built from — not
inside a live-booted Firecracker guest, and not the Tier 2 `vsock`-relay bridge
itself. Both flagged as still-open in "What was NOT explored" below.

## Result

**Answered, and better than expected on both counts.**

## Findings

### Footprint — smaller than the apt dependency chain suggested

The system `libzmq5` package (Ubuntu 24.04) pulls in real dependencies:

| Package | Installed size | Needed for `ipc://`? |
|---|---:|---|
| `libzmq5` | 698 KB | yes — core |
| `libbsd0` | 127 KB | yes — base |
| `libgssapi-krb5-2` (Kerberos) | 426 KB | **no** — only for ZMQ's GSSAPI mechanism |
| `libnorm1t64` (NORM multicast) | 388 KB | **no** — only for multicast transports |
| `libpgm-5.3-0t64` (PGM multicast) | 303 KB | **no** — only for multicast transports |
| `libsodium23` (CURVE crypto) | 401 KB | **no** — only for ZMQ's CURVE security |

Full chain: ~2.3MB. Only ~825KB of that (`libzmq5` + `libbsd0`) is actually needed
for the plain `ipc://` Unix-socket case this design uses. `libc6`/`libgcc-s1`/
`libstdc++6` are already present on any base Linux system — zero marginal cost.

**Better finding: this analysis is moot for the actual Python path.** `pyzmq`'s
published wheel **bundles its own statically-compiled `libzmq`** — `import zmq`
worked in a fresh virtualenv with **zero system `libzmq5` installed at all**.
Total footprint: **1.6MB**, self-contained, no Kerberos/multicast/CURVE bloat to
worry about since the bundled build doesn't pull in the system package's
dependency chain. Since Fabrica's guest-side shim is Python (per
`tool-execution.md`'s "Python first" direction), this is the actually-relevant
number: **1.6MB, one `pip install pyzmq`** (or bundling the wheel into the guest
image at build time), not a multi-package apt dependency tree.

### Functional test — real `ipc://` round trip

A real `REQ`/`REP` pair over `ipc:///tmp/fabrica-spike.sock`, sending a JSON
payload matching the shape of the actual callback (`{"tool": ..., "params": ...}`)
and receiving a result:

```
server listening on ipc:///tmp/fabrica-spike.sock
server received: {'tool': 'read_file', 'params': {'path': '/etc/hostname'}}
client received: {'result': "processed: read_file({'path': '/etc/hostname'})"} (round trip: 0.73ms)
```

**0.73ms round trip.** Negligible overhead for the Tier 0/1 callback mechanism —
this is a non-issue relative to everything else already measured in this project
(Firecracker restore 8–11ms, `srt` p50 152ms).

## Evidence

Environment: `kodiak@darkenergy`, Ubuntu 24.04.4 LTS x86_64 — the same OS/arch
the earlier Firecracker spike's rootfs was built from, though this spike ran on
the host directly, not inside a booted guest (see scope note above).

```
pyzmq version: 27.1.0
libzmq version: 4.3.5
1.6M  <venv>/lib/python3.12/site-packages/zmq
```

## Implications for the plan

- **Tier 0/1's ZMQ `ipc://` design is confirmed practical**, not just
  architecturally clean. Fast, tiny footprint, no relay needed — matches
  `system-design.md §3` exactly as designed.
- **Guest image builds should bundle the `pyzmq` wheel directly**, not depend on
  the guest OS's package manager or system `libzmq5` — smaller, simpler, and
  avoids the multicast/Kerberos/CURVE dependencies entirely.
- This derisks the ZMQ choice enough to proceed with confidence on Tier 0/1.
  Tier 2 remains a separate, harder question (see below) — this spike
  deliberately didn't attempt it.

## What was NOT explored

- **Inside an actual booted Firecracker guest.** This ran on the same OS/arch as
  the guest rootfs, on the host directly — not inside a live microVM. A minimal
  guest's actual available packages/libc version could differ from the full
  Ubuntu host environment used here.
- **The Tier 2 relay itself** — whether `pyzmq` (or `libzmq` generally) can be
  bridged to `vsock`/`VZVirtioSocketDevice`/`AF_HYPERV` by a small relay process,
  which is the harder, more load-bearing half of `system-design.md §3`'s
  resolved design. This spike only validated the *guest-local* ZMQ side, not the
  cross-VM-boundary bridge.
- **Concurrent/load behavior** — a single request/response pair was tested, not
  what happens under the callback volume a real code-mode run might generate
  (many tool calls in a loop).
- **macOS/Windows guest environments** — this spike ran entirely on Linux; no
  equivalent check was done for what a libkrun or Hyper-V guest's Python/pyzmq
  footprint would look like.

## Recommendation

**Proceed with ZMQ for Tier 0/1 — confirmed practical, not just clean on paper.**
The Tier 2 relay bridge (vsock/VZVirtioSocketDevice/AF_HYPERV) remains the real
open engineering work and deserves its own dedicated spike or prototype once
implementation begins — this one deliberately stayed scoped to the easier,
already-resolved half of the design rather than attempting both at once.
