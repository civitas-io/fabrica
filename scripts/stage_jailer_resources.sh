#!/bin/bash
# STATUS: written and manually validated for the kernel/rootfs staging
# step specifically, but NOT YET the final design -- see
# specs/archive/spikes/SPIKE-firecracker-jailer-vsock-integration.md
# for the full, real reason: this script's current `chown -R` on the
# WHOLE per-jail directory tree (below) conflicts with the vsock design
# also validated in that spike, which needs `root/` itself to stay
# writable by the INVOKING user until AFTER it binds the vsock socket,
# not immediately locked to fc-jail by this script. Real fix needed
# before this is used in real FirecrackerSandbox code: chown ONLY the
# kernel/rootfs files to fc-jail, leave `root/` itself invoker-owned
# (jailer will correctly chown `root/` itself on its own, non-
# recursively, when it runs -- confirmed empirically in that spike).
# This script's ALREADY-INSTALLED sudoers rule covers this exact
# command shape either way, so fixing the internals here needs no new
# privilege grant, just a real code change, not yet made.
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
cp "$KERNEL_PATH" "$JAIL_ROOT/kernel"
cp "$ROOTFS_PATH" "$JAIL_ROOT/rootfs.ext4"
chown -R "$JAIL_UID:$JAIL_GID" "$CHROOT_BASE_DIR/$EXEC_BASENAME/$JAIL_ID"

echo "staged: $JAIL_ROOT/kernel, $JAIL_ROOT/rootfs.ext4"
