# Spike: does a live vsock connection survive Firecracker snapshot/restore into a fresh process?

**Status:** Complete · **Script:** [scripts/spike-firecracker-snapshot-restore-vsock/](scripts/spike-firecracker-snapshot-restore-vsock/)

---

## Locked question

[SPIKE-firecracker-boot-restore-latency.md](SPIKE-firecracker-boot-restore-latency.md)
validated snapshot/restore timing (restore: single-digit ms) on a **bare**
VM — no guest activity, no vsock connection. Separately,
[SPIKE-firecracker-vsock-callback-bridge.md](SPIKE-firecracker-vsock-callback-bridge.md)
validated a real, bidirectional vsock tool-call bridge — on a **cold-booted**
VM, never combined with snapshot/restore. `FirecrackerSandbox` v1 ships
cold-boot-only specifically because this combination was named, explicitly,
as untested (`docs/contracts/sandbox.md`, `PLAN.md` item 20): does a guest
that's mid-request — connected over vsock, blocked waiting for the next
message — actually survive being snapshotted and restored into a **fresh**
Firecracker process? Neither existing spike answers this.

**Question:** snapshot a guest while its vsock connection is live (blocked
in `recv()`, exactly the state a real warm-pool member would be in between
requests), restore that snapshot into a brand-new `firecracker` process, and
see what actually happens — not inferred from either spike alone.

## Method

Same homelab as both prior spikes (`kodiak@darkenergy`, bare-metal AMD-V,
Ubuntu 24.04, KVM), same kernel/rootfs artifacts. Real `curl`-driven
Firecracker REST calls, matching `firecracker_backend.py`'s own mechanism
exactly (not a different toy harness):

1. Boot a guest, wait for the real guest-shim's `{"type": "ready"}` message
   over a real vsock connection (guest now blocked in `recv()`, waiting for
   `{"type": "code", ...}` — the exact state a warm sandbox sits in).
2. `PATCH /vm {"state": "Paused"}`, then `PUT /snapshot/create`.
3. Kill the original `firecracker` process with `SIGKILL` — deliberately,
   not a graceful shutdown, matching how a real crash or a real
   `SandboxPool.terminate()` actually ends a process's life.
4. Start a **second, independent** `firecracker` process with its own API
   socket, set up a fresh vsock host-side listener, and `PUT /snapshot/load`
   with `resume_vm: true`.
5. Check whether the OLD accepted connection still works, and whether a
   NEW connection arrives on the fresh listener.

## Result

**Answered, and more precisely than "does it work": it doesn't work with
today's shim, but the failure is a real, understood, fixable gap — not a
fundamental one.**

### First real finding: a stale vsock socket file blocks restore entirely

Restoring into a fresh process failed immediately, before anything
guest-side even mattered:

```
"fault_message":"Load snapshot error: Failed to restore from snapshot:
Failed to build microVM from snapshot: Failed to restore devices: Error
restoring MMIO devices: VsockUnixBackend: Error binding to the host-side
Unix socket: Address in use (os error 98)"
```

Firecracker's own vsock device binds a Unix socket at the **base**
`uds_path` itself, not just the per-port `{uds_path}_{port}` files
`asyncio.start_unix_server` listens on. `SIGKILL` gives the original
process zero chance to clean that file up, so it's left occupying the path
on disk — confirmed directly (`ls -la` showed the stale `vsock.sock` file
still present after the kill). **Fix: delete the base `uds_path` file
before restoring into a new process.** A real, simple, one-line operational
requirement, not a design problem.

### Second, bigger finding: the guest kernel-panics on resume — a real, understood, fixable cause

With the stale socket removed, `snapshot/load` succeeded (8.8ms — the
single-digit/low-double-digit-ms figure holds even here). But the **guest**
crashed immediately on resume:

```
File "/tmp/guest_shim.py", line 57, in _recv_exact
    chunk = sock.recv(remaining)
ConnectionResetError: [Errno 104] Connection reset by peer
[    2.916304] Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000100
```

This is the correct, expected kernel behavior once you know the shape of
the bug — **not** memory corruption or an unrecoverable vsock design flaw:

1. The guest was blocked in `sock.recv()` at snapshot time.
2. On resume, the guest kernel's vsock layer correctly recognizes its old
   peer no longer exists (a genuinely fresh vsock device backs the new
   process) and delivers a real `ECONNRESET` to the blocked call — the
   textbook-correct response to a broken connection.
3. `_firecracker_guest_shim.py` has **zero error handling** around its
   socket calls. The unhandled exception kills the Python process — which
   is PID 1 (`init=/usr/bin/python3`) — and Linux panics rather than allow
   PID 1 to exit.

### Third finding: the fix works, verified, not theorized

Patched a throwaway copy of the guest shim
(`scripts/spike-firecracker-snapshot-restore-vsock/patched_guest_shim.py`)
with a real reconnect loop: catch `OSError` around the initial
connect/ready/wait-for-code sequence, open a **fresh** `AF_VSOCK` socket,
and retry. Baked it into a rootfs copy via the existing
`build_firecracker_rootfs.sh` mount/cp mechanism (no new sudo scope needed)
and re-ran the exact same snapshot/restore sequence:

```
guest sent: {'type': 'ready', 'reconnect_attempt': 1}      # original, pre-snapshot
... snapshot / kill / fresh process / restore ...
NEW connection arrived! Guest reconnected after restore.
guest sent on new connection: {'type': 'ready', 'reconnect_attempt': 2}
```

**The guest genuinely detects the reset, reconnects with a fresh socket,
and re-signals `ready` to the new process's fresh listener.** No kernel
panic, no manual intervention. The combination works — it just needs the
guest shim hardened, which it wasn't built to be (it was never designed
against restore in the first place; that was always this spike's own job
to find out).

## What this doesn't resolve — real, separate follow-up work, not done here

- **The real fix hasn't been ported into `_firecracker_guest_shim.py`
  itself** — this spike proves the mechanism, using a disposable copy
  (`patched_guest_shim.py`), not a permanent change to shipped code.
- **`FirecrackerSandbox` has no `boot_from_snapshot()`/restore API at
  all** — `boot_clean()` always cold-boots. Wiring a real restore path
  into the backend, `SandboxPool`'s warm-pool refill logic, and the
  stale-socket-file cleanup this spike found is real, separate
  implementation work.
- **The reconnect loop's retry timing here (`time.sleep(0.2)`) is a
  throwaway placeholder**, not tuned for latency — a real implementation
  should retry near-instantly (no fixed backoff, or a much smaller one) to
  preserve as much of the single-digit-ms restore benefit as possible;
  this spike measured that the mechanism WORKS, not its optimal latency.
- **Only tested the "blocked waiting for `code`" state** — the exact state
  a warm-pool member sits in between requests, and the one that matters
  for `SandboxPool`'s use case. A guest snapshotted mid-tool-call (blocked
  in a different `recv()`, further into the protocol) was not tested
  separately; the same fix (a general reconnect-and-resume-the-protocol
  loop, not just at startup) would need to cover that state too if
  snapshotting is ever considered anywhere but the pre-request warm state.

## Evidence

Scripts: `specs/archive/spikes/scripts/spike-firecracker-snapshot-restore-vsock/`
(`spike.py`, `patched_guest_shim.py`). Console log excerpts (the kernel
panic and the successful reconnect) are quoted in full above, not
paraphrased.
