# Building a `FirecrackerSandbox` rootfs image

**Status:** Implemented · **Last updated:** 2026-08

This is the real, reusable, documented version of the exact procedure
validated by hand on a real homelab in
[SPIKE-firecracker-vsock-callback-bridge.md](https://github.com/civitas-io/fabrica/blob/main/specs/archive/spikes/SPIKE-firecracker-vsock-callback-bridge.md).
It closes the one real reproducibility gap left after `FirecrackerSandbox`
shipped: without this, the only way to produce a working rootfs image was
a one-off set of manual commands run by hand on one specific machine —
not something a second deployer could actually follow.

## What you need before running this

1. **A base rootfs image** — any bootable `ext4` image with a real Python
   3 interpreter on `PATH` inside it. The original validated spike used a
   stock Ubuntu 24.04 cloud image (1GB). **A real, minimal, purpose-built
   base now exists too** — see "Building a minimal base image" below —
   this script works with either; a general-purpose OS image is no
   longer the only option, just the one the spike happened to use first.
2. **A kernel image** compatible with Firecracker (the validated spike
   used `vmlinux-6.1.177` — see the spike doc for exactly how that was
   built).
3. **The `firecracker` binary itself**, on the host that will run
   `FirecrackerSandbox`.
4. **Real KVM** (`/dev/kvm` present, read/write access) on that host.
5. **Narrowly-scoped `sudo` access** — see below. Not blanket root.

## Scoped `sudo` rules — copy these into a `sudoers.d` file

Real, minimal capabilities only, matching this project's own
fail-closed-by-default discipline applied to infrastructure access, not
just code. Replace `yourusername` and adjust the mount point if you use
something other than `/mnt/fcrootfs`:

```
yourusername ALL=(root) NOPASSWD: /usr/bin/mount -o loop * /mnt/fcrootfs
yourusername ALL=(root) NOPASSWD: /usr/bin/umount /mnt/fcrootfs
yourusername ALL=(root) NOPASSWD: /usr/sbin/losetup *
yourusername ALL=(root) NOPASSWD: /usr/bin/cp /tmp/*.py /mnt/fcrootfs/*
```

Verify general `sudo` still requires a password afterward — these rules
should be additive, not a blanket grant. Create the mount point once:

```
sudo mkdir -p /mnt/fcrootfs
```

## Building a minimal base image (recommended over a general-purpose OS image)

```
scripts/build_firecracker_minimal_base.sh <output-base.ext4> [size-mb, default 300]
```

Builds a real Ubuntu 24.04 + `python3`-only image via a real Docker
build (`scripts/firecracker-minimal-base.Dockerfile`), exports its
filesystem, and populates a sized `ext4` image from it via `mke2fs -t
ext4 -d <dir>` -- entirely in userspace, no mount, no loop device, no
root. Needs `docker` (the invoking user in the `docker` group, not
root) and `mke2fs` (`e2fsprogs`) -- **no new `sudo` scope beyond what's
already documented above**; this script only produces a base image,
the same shape of input a stock Ubuntu cloud image already was.

**A real dead end tried first, worth knowing about**: the obvious
choice, the official `python:3.12-slim` Docker image, does NOT work.
It installs Python into `/usr/local/bin/python3.12` (built from source
at that image's own build time), not `/usr/bin/python3` --
`FirecrackerSandbox`'s kernel boot args (`firecracker_backend.py`) are
fixed at `init=/usr/bin/python3`, not configurable per rootfs. Booting
a guest built from `python:3.12-slim` produces a real kernel panic,
confirmed on real hardware: `Requested init /usr/bin/python3 failed
(error -2)` (`ENOENT`). Building from plain `ubuntu:24.04` +
`apt-get install python3` instead installs the standard distro way --
`/usr/bin/python3` -> `/usr/bin/python3.12` -- matching the fixed boot
args with no code change and no custom symlink step needed.

**Real, measured result** (homelab, real KVM, real Firecracker --
numbers, not estimates): apparent image size **1.0G -> 300M**; actual
on-disk size (the file is sparse) **170M -> 60M**. The per-instance
rootfs COPY `firecracker_backend.py`'s `boot_clean()` does for every
sandbox instance dropped from a consistent **~945-955ms to ~265-268ms**
(3 repeated runs each, same session, same disk) -- roughly proportional
to apparent file size, since `shutil.copyfile()` does not preserve
sparseness. End-to-end `boot_clean()` (copy + real VM boot + real vsock
handshake, fair back-to-back same-session comparison, 3 runs each): OLD
**~1910ms avg -> NEW ~1205ms avg**, a real **~37% reduction**.

**Stated honestly, not oversold**: guest kernel BOOT TIME itself did
not meaningfully change between the two images (dominated by kernel
init/`devtmpfs`/Python interpreter startup, not rootfs size) -- this is
a real disk-footprint and per-instance-copy win, not a kernel-boot-
latency win.

Verified working end to end on real hardware before this script or its
Dockerfile existed, not assumed correct because it booted: a real
`print()`, a real stdlib import smoke test (`re`, `hashlib`,
`datetime`, `json`, `base64`, `itertools`, `collections`, `math`,
`random`, `uuid`, `urllib.parse`), a real tool-call round trip over
`vsock`, and confirming `/dev/null`/`/dev/urandom` both exist and are
readable (`devtmpfs` auto-mount, confirmed via the guest's own console
log -- not assumed present in a minimal image just because it usually
is). The full existing `test_firecracker_backend.py` suite (16 tests --
CPU accounting, network-isolation, timeout handling, the real tool-call
boundary crossing) was then run against a base image built by this
exact script, end to end, not just the manual steps that produced it
the first time -- 16/16 passed.

## Baking the guest shim in

```
scripts/build_firecracker_rootfs.sh <base-rootfs.ext4> <output-rootfs.ext4> /mnt/fcrootfs
```

This copies the base image, mounts the copy, bakes
`src/fabrica/sandbox/_firecracker_guest_shim.py` in at `/tmp/guest_shim.py`
(exactly the path `FirecrackerSandbox`'s default `guest_shim_path` expects),
and unmounts — always, even on failure, so a partial run doesn't leave the
mount point blocked for the next attempt.

A fresh copy is made every run rather than mutating the base image in
place, matching a real finding from the underlying spike: a rootfs that
has already been booted once may need ext4 journal recovery on next boot,
which fails outright against a read-only mount. Keeping the base image
pristine and always copying from it avoids this class of bug entirely.

## Using the result

Point `FirecrackerSandbox` (or the `FABRICA_FC_ROOTFS` environment
variable `select_sandbox_backend()` reads, see
[isolation.md](../isolation.md)) at `<output-rootfs.ext4>`, alongside
`FABRICA_FC_BINARY` and `FABRICA_FC_KERNEL`. Real platform dispatch
(`contracts/civitas-bridge.md`) picks `FirecrackerSandbox` up
automatically once all three are set and the paths genuinely exist.

## What this deliberately does not cover

- **Kernel image build steps** — assumed already available; see the
  spike doc for how the validated one was produced.
- **Image signing / supply-chain verification** for the resulting
  artifact — flagged in `HANDOFF.md` as a longer-term product decision
  not yet made, not assumed here.
