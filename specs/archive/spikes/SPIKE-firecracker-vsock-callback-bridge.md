# Spike: does a real Firecracker guest↔host `vsock` tool-call bridge actually work?

**Status:** Complete · **Timebox:** ~1 hour · **Script:** [scripts/spike-firecracker-vsock-callback/run_vsock_roundtrip.sh](scripts/spike-firecracker-vsock-callback/run_vsock_roundtrip.sh)

---

## Locked question

[SPIKE-firecracker-boot-restore-latency.md](SPIKE-firecracker-boot-restore-latency.md)
validated Firecracker's boot/snapshot/restore timing on real hardware, but
explicitly named what it did **not** test: *"Networking — booted with no TAP
device/network interface at all... a real sandbox would need this wired up,
with its own setup cost not measured here."* `contracts/sandbox.md`'s entire
`on_tool_call` mechanism — the thing that makes code-mode actually able to
call real tools — depends on SOME host↔guest channel existing. For
`SubprocessSandbox` that's a real, working ZMQ `ipc://` socket. For a
self-hosted Firecracker backend, `isolation.md` names `vsock` as the intended
mechanism, but it had never been built or tested at all — a real, structural
unknown, not a detail.

**Question:** does a real `AF_VSOCK` connection between a process running
inside a Firecracker guest and a process on the host actually work — real
bidirectional data exchange, not just the device configuring without error —
on the same real hardware validated before?

## Method

Same homelab as the boot/restore spike (`kodiak@darkenergy`, bare-metal AMD-V,
Ubuntu 24.04, KVM). Reused the existing Firecracker binary, kernel
(`vmlinux-6.1.177`), and base rootfs (`ubuntu-24.04.ext4`) from that spike's
environment, still present and live.

1. Confirmed the kernel has real, working `vsock` support: `PUT /vsock` on
   Firecracker's real API (`{"guest_cid": 3, "uds_path": "..."}`) succeeded,
   the guest's own boot console showed `NET: Registered PF_VSOCK protocol
   family` automatically, and Firecracker created the expected host-side
   Unix domain socket for the vsock device.
2. Started a real Python `AF_UNIX` listener on the host at
   `{uds_path}_5555` — Firecracker's real, documented convention for where a
   **guest-initiated** vsock connection to host port 5555 gets proxied to.
   This has to exist *before* the guest tries to connect; it's not a
   coincidence of preexisting infra, it's how Firecracker's vsock device
   actually works.
3. Booted the guest straight into `/bin/sh` (`init=/bin/sh` boot arg) instead
   of full systemd, with `firecracker`'s own stdin wired to a FIFO so shell
   commands could be sent interactively over the real serial console —
   avoided needing to bake anything into the rootfs image at all for this
   specific test (see the negative finding below for why that would have
   been harder than expected anyway).
4. From inside that shell, ran a one-line real Python script:
   `socket.socket(AF_VSOCK, SOCK_STREAM).connect((2, 5555))` (CID 2 =
   `VMADDR_CID_HOST`, the fixed address for "the host" from any guest's
   perspective), sent a real string, and printed whatever came back.

## Result

**Answered, real bidirectional data exchange confirmed:**

```
=== HOST LISTENER OUTPUT ===
HOST: listening
HOST GOT: b'hello from guest\n'
=== GUEST CONSOLE OUTPUT (tail) ===
...
# python3 -c "import socket; s=socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM); s.connect((2,5555)); s.sendall(b\"hello from guest\n\"); print(\"GUEST_GOT:\", s.recv(1024))"
GUEST_GOT: b'hello from host\n'
```

Guest → host: the host's real Python socket received exactly the bytes the
guest sent. Host → guest: the guest's real Python socket received exactly
the bytes the host sent back. This is the literal mechanical shape
`contracts/sandbox.md`'s `on_tool_call` bridge needs — a real request from
inside the sandbox, reaching a real host-side handler, with a real response
returning — proven over `vsock` specifically, not assumed to work "because
ZMQ over `ipc://` worked" (a different transport, same-host case).

## A real negative finding along the way, not hidden

The first attempt tried to inject a custom init script directly into the
existing `ubuntu-24.04.ext4` image using `debugfs -w` (no `sudo`/mount
available without an interactive password prompt). This **partially
failed in a genuinely confusing way**: `debugfs -R "ls root"` showed the
injected file in the directory listing, but `debugfs -R "cat root/<file>"`
immediately afterward reported `File not found by ext2_lookup` — a real
directory/inode inconsistency, most likely from `debugfs -w`'s known
limitations against modern ext4 features (`metadata_csum`, 64-bit) that
Ubuntu 24.04's own `mkfs.ext4` defaults enable. **`debugfs -w` is not a
reliable way to bake custom content into this rootfs image** — worked
around here by not needing to inject anything at all (`init=/bin/sh` +
driving the console interactively instead), but a REAL guest-shim process
that starts automatically at boot (the actual production need) will require
a proper, root-based image-build step (a real mount, or a purpose-built
minimal rootfs/init — already flagged as its own scope item in
`SPIKE-firecracker-boot-restore-latency.md`), not ad-hoc runtime patching.

A second, smaller finding: booting with `is_read_only: true` on a rootfs
that needs ext4 journal recovery fails outright (`write access unavailable,
cannot proceed`) — a real, sharp-edged interaction between Firecracker's
read-only drive flag and an unclean-shutdown ext4 image, not something
either tool's docs called out. Fixed by using a fresh read-write copy per
boot (matching `SandboxPool`'s own always-terminate-never-reuse philosophy
for a different reason: a used rootfs needs recovery, a copy doesn't).

## What this validates, precisely — and what it doesn't

**Validates:** the core mechanism `isolation.md`'s Tier 2 design depends on
— a Firecracker guest can reach a real host-side handler over `vsock`, with
real data flowing both directions, confirmed on real hardware, not assumed
from the vsock device configuring without error.

**Does NOT validate:** a real guest-shim process (the actual production
analog of `fabrica/sandbox/_guest_shim.py`, auto-starting at boot and
implementing the full request/response protocol `on_tool_call` needs, not
a one-off interactive shell command); how that shim gets baked into a real,
maintained rootfs image (the `debugfs -w` negative finding above means this
needs a proper build step, not a quick patch); performance (this spike
proved correctness, not latency, of the vsock round trip); concurrent
guests each with their own vsock CID (only one guest was ever running here).

## Recommendation

**Proceed with `vsock` as `FirecrackerSandbox`'s host↔guest transport** — the
core viability claim is real, measured, and credible, the same verdict the
boot/restore spike reached for Firecracker's timing claims. Before writing
`FirecrackerSandbox` for real:

1. A real guest-shim process (mirroring `_guest_shim.py`'s actual protocol,
   not a one-off `python3 -c`) needs to be built and get a real way into the
   rootfs image — the `debugfs -w` approach is confirmed unreliable; a
   proper root-based build step is real, separate work.
2. The host-side listener (`{uds_path}_{port}`, pre-created before boot) is
   the correct pattern `FirecrackerSandbox.boot_clean()`/`execute()` should
   follow — this is now a proven, not a guessed, integration point.
3. `jailer` remains unexplored (explicitly out of scope in both this spike
   and the earlier one) — a real, separate item for later, not resolved here.
