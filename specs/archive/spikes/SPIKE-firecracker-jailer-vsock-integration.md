# Spike (IN PROGRESS): real `jailer` integration for `FirecrackerSandbox` — the vsock-inside-a-locked-chroot problem

**Status:** Research + empirical validation complete for the core mechanism. Implementation NOT YET STARTED. This doc exists so the next session can resume without re-deriving any of this — see "Exact next steps" at the bottom.

---

## The question

PLAN.md item 21: real `jailer` integration for `FirecrackerSandbox` (defense-in-depth hardening — chroot, cgroups, uid/gid drop, unexplored in both prior Firecracker spikes). Decided directly with the user: **cold-boot only for now, not combined with snapshot/restore** — "as long as the scripts/code being executed within the jailed env cannot jailbreak, we are good. Snapshot is good to have, but not at the expense of security."

## Real infrastructure already set up on the homelab (`kodiak@darkenergy`)

Three real, scoped sudoers rules exist, installed via a real, idempotent bootstrap script (`scripts/setup_firecracker_jailer.sh`, already committed... **NOT YET COMMITTED, still in working tree / only exists as validated design** — verify `git status` on resume):

1. `/etc/sudoers.d/fabrica-jailer` — start a jailed instance: `jailer --id * --exec-file /home/kodiak/bin/firecracker --uid 61000 --gid 61000 --chroot-base-dir /srv/jailer -- *`
2. `/etc/sudoers.d/fabrica-jailer-terminate` — kill one: `pkill -9 -u fc-jail -f -- --id\ *\ --start-time-us`
3. `/etc/sudoers.d/fabrica-jailer-stage` — stage kernel+rootfs into a new jail before boot: `scripts/stage_jailer_resources.sh /srv/jailer firecracker * /home/kodiak/fc-spike/vmlinux-6.1.177 * 61000 61000`

Dedicated low-privilege identity: `fc-jail` user/group, uid=gid=61000, created via the bootstrap script. Chroot base: `/srv/jailer`, mode 700 root-owned.

**Two real scripts exist** (`scripts/setup_firecracker_jailer.sh`, `scripts/stage_jailer_resources.sh`) — both written, both validated manually on the homelab, but **`stage_jailer_resources.sh` needs a real design change before use** (see below) — its CURRENT committed-to-disk version does a `chown -R` of the whole per-jail directory tree to `fc-jail`, which conflicts with the vsock design (see next section). This has NOT been fixed yet.

## The hard problem, and how it was solved — READ THIS BEFORE IMPLEMENTING

Firecracker's whole filesystem view starts at the jail's `root/` directory once chrooted. The vsock host-side socket (where the guest connects out to) must therefore live *inside* `root/` — but `jailer` locks `root/` down to `700 fc-jail:fc-jail` as part of its own setup, and the ORCHESTRATOR (`kodiak`, unprivileged) needs to `bind()`+`accept()` on that socket continuously, from a completely different, non-privileged identity.

**Real findings, from Firecracker's own official docs (`jailer.md`, `prod-host-setup.md`, `vsock.md`) plus direct empirical testing on real hardware — not assumed from any single source:**

1. **The operator is part of the trusted computing base** (Firecracker's own `jailer.md`, verbatim: *"The operator invoking the jailer is part of the trusted computing base."*). The jailer's security boundary is guest-vs-host, not operator-vs-jail-directory. This matters because it means the ORCHESTRATOR having elevated access to jail internals is not a violation of the threat model.

2. **`jailer`'s chown/chmod pass on `root/` is NOT recursive.** Proved directly: pre-staged a test file and a bound Unix socket, both `kodiak`-owned, inside `root/` before running `jailer`. After `jailer` ran, `root/` itself became `700 fc-jail:fc-jail`, but the pre-existing files kept their original `kodiak:kodiak` ownership untouched. (Test scripts used for this are NOT preserved anywhere — they were disposable, run via one-off `bash -c` invocations over SSH, not archived. If re-validation is ever needed, recreate from the description here.)

3. **Unix permission checks happen at `bind()`/`connect()` time, not on every subsequent operation via an already-open file descriptor.** Standard Unix property, verified to apply here specifically: a listening socket `kodiak` opened *before* `jailer` locked `root/` down continued to `accept()` new connections fine *after* the lockdown.

4. **The actual, only real bug (not a fundamental wall): AF_UNIX `connect()` requires WRITE permission on the target socket file itself, not just traversal permission on its directory.** Python's default `socket.bind()` leaves the file at `srwxrwxr-x` (owner/group get rwx, "other" gets only r-x — no write). Since `fc-jail` is neither the socket's owner nor its group, it's evaluated as "other" and was blocked — which surfaced as a real `ConnectionResetError` inside the guest (Firecracker translates "can't reach anyone at that path" into a `VIRTIO_VSOCK_OP_RST`, which the guest kernel's vsock stack surfaces as `ConnectionResetError`, not `ConnectionRefusedError`). This cascaded into a real kernel panic (`Attempted to kill init!`) once the guest shim's 200 reconnect attempts were exhausted (~1.1s wall time, matching the retry backoff math exactly) — **the guest shim's reconnect logic itself worked correctly and was not the bug.**

**The validated fix, proven end to end on real hardware, with a real guest booting through the real jail boundary:**

1. `kodiak` binds + listens the vsock socket **inside `root/`, before `jailer` runs** (while that directory is still `kodiak`-writable — this requires `root/` to NOT already be chowned away from kodiak by the staging step, see the design-change note below).
2. `kodiak` explicitly `os.chmod(path, 0o777)`s that socket file.
3. `jailer` runs, locks `root/` down to `700 fc-jail:fc-jail` — confirmed the pre-existing socket survives untouched.
4. The guest connects out (CID=2); Firecracker's vsock proxy (running as `fc-jail`, which owns `root/` and can traverse it, and now has write access to the world-writable socket) successfully reaches `kodiak`'s already-bound listener.
5. `kodiak`'s `accept()` — on the fd opened before the lockdown — fires normally. **Confirmed with a real message: `GUEST SENT: {'type': 'ready'}`.**

**No new sudo rule was needed for this.** The three already-approved rules are sufficient — the trick is purely about *timing* (bind before lockdown) and *permissions* (explicit `chmod 0o777` on the socket), not new privilege.

## Why `chmod 0o777` on the vsock socket is not actually a security concern

A world-writable *socket* means "anyone who can reach this path can connect to it" — but reaching it requires traversing `root/` first, which stays locked to `fc-jail` only. The directory's own restriction is the real boundary; the socket's permissive bits are irrelevant to anyone who can't get past that directory anyway.

## One real, pre-existing, named limitation (not introduced by this work)

All jails currently share **one** `fc-jail` uid/gid (61000), for simplicity. Firecracker's own `prod-host-setup.md` recommends per-instance unique uid/gid pairs specifically so that if one jail is broken out of, it can't touch another jail's resources (which, under a shared uid, it structurally could — both `root/` directories would be owned by the same `fc-jail` identity). This is a real, already-known tradeoff from the shared-uid decision, not something the vsock fix changes. Worth revisiting if this ever needs to harden further (a uid pool, one per concurrent instance), but not blocking for a first real implementation.

## The second problem, solved but NOT YET VALIDATED empirically: the API socket

Unlike vsock, `kodiak` cannot use the "bind before lockdown" trick for Firecracker's own **API socket** (`api.sock`) — that socket is bound by **Firecracker itself** (running as `fc-jail`), not by `kodiak`, and it doesn't exist until *after* `jailer` has already chrooted+dropped privileges+exec'd. There's no window where `kodiak` could pre-bind it. A fresh `curl --unix-socket` call after boot would hit the exact same directory-traversal wall vsock did — and this time there's no "already-open fd" trick available, since `kodiak` is the *client*, not the *server*, for this socket.

This looked like it would need a **4th sudo rule** (scoped `curl -X PUT --unix-socket <jail-path>/api.sock ...`) — genuinely harder to scope narrowly than the first three, since request bodies vary per call.

**Real, better fix found via research, NOT YET validated empirically**: Firecracker supports `--config-file <path>`, confirmed present on the real installed v1.16.1 binary (`firecracker --help` output: `--config-file <config-file>  Path to a file that contains the microVM configuration in JSON format.`). This is a single static JSON document — boot-source, drives, machine-config, **and vsock** all in one file — read once at process launch and applied in a single pass, with **no separate `InstanceStart` API call at all**: reading the file to completion *is* what starts the machine (confirmed via real research: Firecracker's own `jailer.md` example pairs `--config-file` with jailer directly; a detailed third-party technical writeup, cross-checked against the real `--help` output rather than trusted alone, confirms the same shape and explicitly includes `vsock: {guest_cid, uds_path}` as a supported top-level key).

**If this works as documented, it eliminates the need for a 4th sudo rule entirely**: the staging script (already privileged, already approved, already runs before `jailer`) can write this config file directly into `root/` at the same time it places the kernel/rootfs — no runtime API calls needed at all. Readiness detection then just reuses the SAME vsock "ready" message already relied on for the non-jailed and cold-boot paths — no need to poll for `api.sock`'s existence (which `kodiac` can't do anyway once `root/` is locked).

**What is NOT yet verified, and is the actual next step**: the real, exact JSON schema `--config-file` expects on THIS installed v1.16.1 binary. The bundled OpenAPI spec (`firecracker_spec-v1.16.1.yaml`) was grepped for `config-file`/`config_file` and returned nothing — the config-file schema is likely a separate Rust struct not exposed in the HTTP API's own OpenAPI spec, so it needs to be found a different way (check `firecracker --help` more fully, check for a bundled JSON schema file, or just empirically test a real minimal config file and see what error messages reveal about required fields).

## Exact next steps, in order

1. **Find the real `--config-file` JSON schema for firecracker v1.16.1** — check `firecracker --help` in full (only a fragment was captured before compaction), search for a bundled schema/example file in the release directory or firecracker's own source docs, or just empirically iterate (start with the illustrative shape from research, real error messages will reveal missing/misnamed fields).
2. **Redesign `scripts/stage_jailer_resources.sh`** (real code change needed, not yet done):
   - Currently does `mkdir -p "$JAIL_ROOT"` then `cp` kernel+rootfs then `chown -R "$JAIL_UID:$JAIL_GID"` on the WHOLE per-jail tree — this last step would lock `kodiak` out of `root/` before it gets a chance to bind the vsock socket. **Must change to**: chown `root/` itself to the INVOKING user (so it stays writable), and chown ONLY the kernel+rootfs files (not the whole tree) to `fc-jail`. `jailer` itself will still correctly chown `root/` to `fc-jail` on its own when it runs (confirmed non-recursive, per finding #2 above) — the staging script doesn't need to do that part.
   - Add writing the `vm-config.json` file (once the schema is confirmed) into `root/`, with all paths relative to the chroot (`/kernel`, `/rootfs.ext4`, vsock `uds_path: "/vsock.sock"`).
3. **Implement `FirecrackerSandbox`'s jailed boot path** (`_boot_jailed_instance()` or similar, new method, mirroring `_cold_boot_instance()`/`_restore_instance()`'s existing shape):
   - New constructor params: `use_jailer: bool = False`, `jailer_binary: str = ""`, `jail_uid: int`, `jail_gid: int`, `chroot_base_dir: str = "/srv/jailer"`, `stage_script: str = ""`.
   - **Guard**: reject `use_jailer=True` + `use_snapshot_restore=True` together at construction time with a clear error — this combination was deliberately never validated (a real, separate, harder combination on top of two already-separately-validated ones), matching the user's own explicit "security over optimization, cold-boot only for now" decision.
   - Compute `jail_root = f"{chroot_base_dir}/{Path(firecracker_binary).name}/{instance_id}/root"`.
   - Invoke the staging script via `sudo -n` with the real per-instance rootfs copy path (or have staging copy directly from `base_rootfs_path` — reconsider whether an intermediate temp copy is needed at all, given staging already does one real copy).
   - Bind + `os.chmod(0o777)` the vsock socket at `{jail_root}/vsock.sock_5555` (or whatever `_HOST_VSOCK_PORT` is) as `kodiak`, BEFORE invoking jailer.
   - Invoke jailer via `sudo -n {jailer_binary} --id {id} --exec-file {firecracker_binary} --uid {jail_uid} --gid {jail_gid} --chroot-base-dir {chroot_base_dir} -- --api-sock /api.sock --config-file /vm-config.json` (exact flag name/shape to confirm in step 1).
   - Wait on the SAME vsock "ready" future already used elsewhere — no `api.sock` existence polling (kodiak can't stat inside `root/` once locked).
   - `terminate()` for a jailed instance: use the validated `sudo -n pkill -9 -u fc-jail -f -- "--id {id} --start-time-us"` pattern (already-approved rule #2).
4. **Real tests on the homelab**, matching the existing `test_firecracker_backend.py` density and discipline (real boot, real tool-call round trip, real termination, filesystem-clean verification, `use_jailer=False` default provably unchanged).
5. **Docs**: `docs/contracts/sandbox.md` (new "real jailer implementation notes" section, mirroring the existing FirecrackerSandbox/snapshot-restore ones), `docs/isolation.md`, `PLAN.md` item 21 (currently still `[ ]` — mark done only once implemented and tested, not just researched).
6. **Commit the two bootstrap/staging scripts** (`scripts/setup_firecracker_jailer.sh`, `scripts/stage_jailer_resources.sh`) — confirm via `git status` on resume whether these were ever actually committed; based on the session's own flow they were written and validated manually but the redesign in step 2 must land before committing, so the committed version matches what's actually used.

## Real homelab state to be aware of on resume

- `fc-jail` user/group (61000/61000), `/srv/jailer` (700 root), three sudoers rules — all real, installed, validated, should still be present.
- No test artifacts should be left over (`/srv/jailer/firecracker/*`, `/tmp/jailer-*`) — every test session's own cleanup ran, but worth a quick `ls`/`sudo find` check on resume regardless, matching this project's own filesystem-cleanliness discipline.
- `docs/PLAN.md` item 21 is NOT yet marked done — it's genuinely in progress, further along than "not started" but not complete.
