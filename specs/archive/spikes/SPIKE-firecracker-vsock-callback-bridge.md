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

## Update: the full production protocol, validated end to end (not just a raw byte exchange)

After the initial round trip above, the user granted a small, precisely
scoped `sudo` capability on the homelab (`NOPASSWD` rules for `mount -o
loop`/`umount` against exactly `/mnt/fcrootfs`, plus `losetup`, plus a
follow-up `cp *.py` rule scoped to the same mount point — not blanket
root access), closing the `debugfs -w` gap named above. This unlocked a
full, real validation using the ACTUAL production shim, not a one-off
interactive command:

- Wrote the real `src/fabrica/sandbox/_firecracker_guest_shim.py` —
  connects out over `vsock` to the host, sends `{"type": "ready"}`,
  receives real code, `exec()`s it with a `namespace` object whose
  `call()` does a real length-prefixed-JSON `tool_call`/`tool_result`
  round trip over the SAME persistent connection, then sends a final
  `{"type": "result", ...}` message — the vsock-native equivalent of
  `_guest_shim.py`'s ZMQ-based protocol (ZMQ has no `vsock` transport
  binding, so a small hand-rolled length-prefixed protocol replaces it;
  everything else about the shape is deliberately identical).
- Properly mounted a FRESH rootfs copy via the new `sudo` access, copied
  the real shim in (to `/tmp` inside the guest image — a real, second
  finding: that path is plain `root:root 755` in a never-booted image,
  NOT world-writable 1777 the way a live-booted system's `/tmp` would be,
  since `systemd-tmpfiles` normally fixes that at boot), unmounted, then
  booted with `init=/usr/bin/python3 -- /tmp/guest_shim.py` — no
  systemd at all, a purpose-built single-shot sandbox image doesn't need
  general-purpose OS services.
- A real, own-test-harness bug caught and fixed along the way, worth
  naming since it wasted a real debug cycle: the host-side listener's
  hardcoded UDS path didn't match the `uds_path` value configured via
  Firecracker's own `/vsock` API call in the boot script — two
  independently-named variables that needed to agree exactly, and
  didn't, on the first real run. Caused a totally real, correctly-
  reported connection failure (nothing listening at the path Firecracker
  actually proxies to), not a vsock or Firecracker problem at all —
  confirmed by fixing the naming mismatch and rerunning successfully
  immediately after.

**Full result, real production shim, real tool call, real everything:**

```
HOST: listening on /tmp/fc-vsock-full.sock_5555
HOST: guest is ready, sending code
HOST: real on_tool_call invoked: add({'a': 2, 'b': 3})
HOST: FINAL RESULT: {
  "type": "result",
  "success": true,
  "stdout": "2 + 3 = 5\n",
  "error_message": null,
  "tool_call_count": 1
}
```

The guest ran real Python code (`namespace.call('add', {'a': 2, 'b': 3})`),
the call crossed the real VM boundary over `vsock`, the host's real
`on_tool_call` handler computed the result, the response crossed back, and
the guest's own `print()` output (`2 + 3 = 5`) came back correctly in
`stdout` — the exact same contract `contracts/sandbox.md`'s `RunResult`
already specifies for `SubprocessSandbox`, now proven for a real
Firecracker microVM too.

## Recommendation

**Proceed with `vsock` as `FirecrackerSandbox`'s host↔guest transport, using
the real shim and protocol validated above** — not just the mechanism in
the abstract, the actual production code. Before/while writing
`FirecrackerSandbox` for real:

1. ~~A real guest-shim process... needs to be built~~ **Done**:
   `src/fabrica/sandbox/_firecracker_guest_shim.py`, validated end to end
   above.
2. The host-side listener (`{uds_path}_{port}`, pre-created before boot) is
   the correct pattern `FirecrackerSandbox.boot_clean()`/`execute()` should
   follow — proven, not guessed, including the exact naming discipline
   needed (the mismatch bug above is a real, worth-remembering trap for
   whoever wires this into the real class).
3. **Baking the shim into a real, maintained rootfs image** now has a
   proven, working procedure (mount via scoped `sudo`, copy, unmount) —
   but this spike used a fresh copy of the EXISTING general-purpose Ubuntu
   24.04 image each time, not a purpose-built minimal image. Building and
   maintaining a real minimal rootfs (named as its own scope item since
   the very first Firecracker spike) is still real, separate work —
   this spike proves the INJECTION mechanism works, not that the current
   base image is the right one to ship.
4. `jailer` remains unexplored (explicitly out of scope in both this spike
   and the earlier one) — a real, separate item for later, not resolved here.
