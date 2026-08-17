# Building a `FirecrackerSandbox` rootfs image

**Status:** Implemented · **Last updated:** 2026-08

This is the real, reusable, documented version of the exact procedure
validated by hand on a real homelab in
[SPIKE-firecracker-vsock-callback-bridge.md](../../specs/archive/spikes/SPIKE-firecracker-vsock-callback-bridge.md).
It closes the one real reproducibility gap left after `FirecrackerSandbox`
shipped: without this, the only way to produce a working rootfs image was
a one-off set of manual commands run by hand on one specific machine —
not something a second deployer could actually follow.

## What you need before running this

1. **A base rootfs image** — any bootable `ext4` image with a real Python
   3 interpreter on `PATH` inside it. The validated spike used a stock
   Ubuntu 24.04 cloud image. A real, minimal, purpose-built image (rather
   than reusing a general-purpose OS) is deliberately **not** built here —
   see `docs/contracts/sandbox.md`'s open items; this script works with
   either, once one exists.
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

## Building the image

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
- **A real, minimal, purpose-built rootfs** (as opposed to a general-
  purpose OS image with a shim copied in) — a named, separate item in
  `contracts/sandbox.md`'s open items, not solved by this script.
- **Image signing / supply-chain verification** for the resulting
  artifact — flagged in `HANDOFF.md` as a longer-term product decision
  not yet made, not assumed here.
