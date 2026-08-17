#!/bin/bash
# Builds a deployable Firecracker rootfs image for FirecrackerSandbox --
# a real, reusable, documented version of the exact procedure validated
# by hand in SPIKE-firecracker-vsock-callback-bridge.md, not a new or
# different mechanism. Takes a base rootfs image (any bootable ext4
# image with a real Python 3 interpreter on PATH) and bakes
# _firecracker_guest_shim.py into it at /tmp/guest_shim.py -- the exact
# path FirecrackerSandbox's default guest_shim_path expects.
#
# Requires real, narrowly-scoped sudo access (NOT blanket root) for
# mount/umount/losetup against ONE fixed mount point, plus a `cp *.py`
# rule -- see docs/deployment/firecracker-rootfs.md for the exact
# sudoers lines to add and why each one is scoped the way it is.
#
# Usage:
#   build_firecracker_rootfs.sh <base-rootfs.ext4> <output-rootfs.ext4> <mount-point>
#
# Example:
#   build_firecracker_rootfs.sh ~/fc-spike/ubuntu-24.04.ext4 \
#       ~/fc-spike/golden-rootfs.ext4 /mnt/fcrootfs

set -euo pipefail

if [ "$#" -ne 3 ]; then
    echo "usage: $0 <base-rootfs.ext4> <output-rootfs.ext4> <mount-point>" >&2
    exit 1
fi

BASE_ROOTFS="$1"
OUTPUT_ROOTFS="$2"
MOUNT_POINT="$3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUEST_SHIM="$SCRIPT_DIR/../src/fabrica/sandbox/_firecracker_guest_shim.py"

if [ ! -f "$BASE_ROOTFS" ]; then
    echo "error: base rootfs not found: $BASE_ROOTFS" >&2
    exit 1
fi
if [ ! -f "$GUEST_SHIM" ]; then
    echo "error: guest shim not found at $GUEST_SHIM -- run this script from a" >&2
    echo "       fabrica checkout, not a standalone copy" >&2
    exit 1
fi
if [ ! -d "$MOUNT_POINT" ]; then
    echo "error: mount point $MOUNT_POINT does not exist -- create it once with" >&2
    echo "       'sudo mkdir -p $MOUNT_POINT' before running this script" >&2
    exit 1
fi
if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    echo "error: $MOUNT_POINT is already mounted -- a previous run may have" >&2
    echo "       failed partway through. Run 'sudo umount $MOUNT_POINT' first." >&2
    exit 1
fi

# The guest shim must be staged under /tmp on the HOST first -- the
# scoped sudoers `cp` rule is deliberately restricted to /tmp/*.py
# sources (see docs/deployment/firecracker-rootfs.md), a real, narrow
# capability rather than "copy anything from anywhere."
STAGED_SHIM="/tmp/_firecracker_guest_shim.py"
cp "$GUEST_SHIM" "$STAGED_SHIM"

echo "==> copying base rootfs ($BASE_ROOTFS -> $OUTPUT_ROOTFS)"
rm -f "$OUTPUT_ROOTFS"
cp "$BASE_ROOTFS" "$OUTPUT_ROOTFS"

echo "==> mounting $OUTPUT_ROOTFS at $MOUNT_POINT"
sudo -n mount -o loop "$OUTPUT_ROOTFS" "$MOUNT_POINT"

# Always unmount, even if the copy below fails -- a mounted rootfs left
# behind blocks every subsequent run (see the mountpoint check above).
cleanup() {
    sudo -n umount "$MOUNT_POINT" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> baking the guest shim in at /tmp/guest_shim.py"
sudo -n cp "$STAGED_SHIM" "$MOUNT_POINT/tmp/guest_shim.py"
sync

echo "==> done: $OUTPUT_ROOTFS is ready for FABRICA_FC_ROOTFS / base_rootfs_path"
