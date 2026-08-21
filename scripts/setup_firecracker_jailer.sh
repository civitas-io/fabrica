#!/bin/bash
# One-time, idempotent bootstrap for real jailer integration
# (PLAN.md item 21) -- creates the dedicated low-privilege user jailed
# Firecracker instances run as, the chroot base directory, and the two
# narrowly-scoped sudoers rules FirecrackerSandbox's jailer support
# needs at runtime. Different in kind from build_firecracker_rootfs.sh/
# build_firecracker_minimal_base.sh: those run as a normal user and only
# ever use ALREADY-approved, narrow sudo rules -- this script is the
# thing that CREATES those rules, so it genuinely needs to run once, as
# real root, explicitly invoked by a human. Not something application
# code should ever trigger itself.
#
# Real, found-by-testing reasoning behind BOTH sudoers rules, not
# guessed: jailer's own process tree forks -- the process a caller
# invokes (via sudo) stays root-owned as a monitor; the actual
# `firecracker` process is a SEPARATE child that fully drops to the
# jailed uid/gid, retaining no trace of the original invoker's identity.
# Confirmed directly: killing the root-owned monitor does NOT kill the
# firecracker child -- it's orphaned, and the invoking user has no
# permission to signal it directly once privileges are dropped. A
# second rule, scoped to `pkill -u <jail-user> -f --id <pattern>`, is
# required for real termination -- bounded to a single, dedicated,
# single-purpose account (this script's own fc-jail user never runs
# anything except jailed Firecracker instances) and a real, observed,
# stable substring of Firecracker's own actual argv shape
# (`--id <id> --start-time-us ...`), not an arbitrary/unscoped kill.
#
# A THIRD rule, scoped to stage_jailer_resources.sh (a small, fixed,
# fully-reviewable script shipped alongside this one -- read it before
# trusting this), is required because Firecracker's whole filesystem
# view starts at the jail's chroot root once jailed -- the kernel image
# and rootfs must exist INSIDE that directory before boot, which is
# created fc-jail-owned, mode 700, the moment jailer runs. Deliberately
# a fixed script path, not a raw `cp`/`mkdir` capability -- the
# sudoers grant's real scope IS that script's own body, auditable in
# full, not a general filesystem primitive.
#
# COLD-BOOT ONLY, deliberately, not a limitation of this script: jailer
# + snapshot/restore together is a real, separate, unvalidated
# combination (a third one, after cold-boot+jailer and cold-boot+
# snapshot-restore) -- not attempted here. Security over optimization,
# per direct user decision: shipping jailer for the case that's real
# and validated now, not blocked on proving a harder combination first.
#
# Usage:
#   sudo scripts/setup_firecracker_jailer.sh <path-to-firecracker-binary> <path-to-kernel-image> [invoking-user] [jail-uid] [jail-gid] [chroot-base-dir]
#
# Example (matches this project's own real homelab validation):
#   sudo scripts/setup_firecracker_jailer.sh /home/kodiak/bin/firecracker \
#       /home/kodiak/fc-spike/vmlinux-6.1.177 kodiak 61000 61000 /srv/jailer

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "error: this script must be run as root (sudo scripts/setup_firecracker_jailer.sh ...)" >&2
    exit 1
fi

if [ "$#" -lt 2 ] || [ "$#" -gt 6 ]; then
    echo "usage: $0 <path-to-firecracker-binary> <path-to-kernel-image> [invoking-user] [jail-uid] [jail-gid] [chroot-base-dir]" >&2
    exit 1
fi

FIRECRACKER_BINARY="$1"
KERNEL_IMAGE_PATH="$2"
INVOKING_USER="${3:-${SUDO_USER:-}}"
JAIL_UID="${4:-61000}"
JAIL_GID="${5:-61000}"
CHROOT_BASE_DIR="${6:-/srv/jailer}"
JAIL_USER="fc-jail"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE_SCRIPT="$SCRIPT_DIR/stage_jailer_resources.sh"

if [ -z "$INVOKING_USER" ]; then
    echo "error: no invoking-user given and \$SUDO_USER is unset -- pass it explicitly" >&2
    echo "       (this happens when running as literal root, not via 'sudo ./script.sh')" >&2
    exit 1
fi
if [ ! -f "$FIRECRACKER_BINARY" ] && ! command -v "$FIRECRACKER_BINARY" >/dev/null 2>&1; then
    echo "error: firecracker binary not found: $FIRECRACKER_BINARY" >&2
    exit 1
fi
FIRECRACKER_BINARY="$(readlink -f "$FIRECRACKER_BINARY")"
JAILER_BINARY="$(dirname "$FIRECRACKER_BINARY")/jailer"
if [ ! -f "$JAILER_BINARY" ]; then
    echo "error: expected jailer alongside firecracker at $JAILER_BINARY -- not found" >&2
    exit 1
fi
if [ ! -f "$KERNEL_IMAGE_PATH" ]; then
    echo "error: kernel image not found: $KERNEL_IMAGE_PATH" >&2
    exit 1
fi
KERNEL_IMAGE_PATH="$(readlink -f "$KERNEL_IMAGE_PATH")"
if [ ! -f "$STAGE_SCRIPT" ]; then
    echo "error: expected stage_jailer_resources.sh alongside this script at $STAGE_SCRIPT" >&2
    exit 1
fi
chmod +x "$STAGE_SCRIPT"

echo "==> ensuring group $JAIL_USER (gid $JAIL_GID) exists"
if getent group "$JAIL_USER" >/dev/null 2>&1; then
    echo "    already exists, skipping"
else
    groupadd --system --gid "$JAIL_GID" "$JAIL_USER"
fi

echo "==> ensuring user $JAIL_USER (uid $JAIL_UID) exists"
if getent passwd "$JAIL_USER" >/dev/null 2>&1; then
    echo "    already exists, skipping"
else
    useradd --system --no-create-home --shell /usr/sbin/nologin \
        --uid "$JAIL_UID" --gid "$JAIL_GID" "$JAIL_USER"
fi

echo "==> ensuring chroot base directory $CHROOT_BASE_DIR exists"
mkdir -p "$CHROOT_BASE_DIR"
chmod 700 "$CHROOT_BASE_DIR"

echo "==> writing scoped sudoers rule for starting jailed instances"
JAILER_RULE_FILE="/etc/sudoers.d/fabrica-jailer"
cat > "${JAILER_RULE_FILE}.tmp" << EOF
$INVOKING_USER ALL=(root) NOPASSWD: $JAILER_BINARY --id * --exec-file $FIRECRACKER_BINARY --uid $JAIL_UID --gid $JAIL_GID --chroot-base-dir $CHROOT_BASE_DIR -- *
EOF
if ! visudo -c -f "${JAILER_RULE_FILE}.tmp" >/dev/null; then
    echo "error: generated sudoers rule failed visudo validation -- not installed" >&2
    rm -f "${JAILER_RULE_FILE}.tmp"
    exit 1
fi
mv "${JAILER_RULE_FILE}.tmp" "$JAILER_RULE_FILE"
chmod 440 "$JAILER_RULE_FILE"
echo "    installed: $JAILER_RULE_FILE"

echo "==> writing scoped sudoers rule for terminating jailed instances"
TERMINATE_RULE_FILE="/etc/sudoers.d/fabrica-jailer-terminate"
PKILL_BINARY="$(command -v pkill)"
cat > "${TERMINATE_RULE_FILE}.tmp" << EOF
$INVOKING_USER ALL=(root) NOPASSWD: $PKILL_BINARY -9 -u $JAIL_USER -f -- --id\ *\ --start-time-us
EOF
if ! visudo -c -f "${TERMINATE_RULE_FILE}.tmp" >/dev/null; then
    echo "error: generated sudoers rule failed visudo validation -- not installed" >&2
    rm -f "${TERMINATE_RULE_FILE}.tmp"
    exit 1
fi
mv "${TERMINATE_RULE_FILE}.tmp" "$TERMINATE_RULE_FILE"
chmod 440 "$TERMINATE_RULE_FILE"
echo "    installed: $TERMINATE_RULE_FILE"

echo "==> writing scoped sudoers rule for staging kernel/rootfs into a new jail"
STAGE_RULE_FILE="/etc/sudoers.d/fabrica-jailer-stage"
cat > "${STAGE_RULE_FILE}.tmp" << EOF
$INVOKING_USER ALL=(root) NOPASSWD: $STAGE_SCRIPT $CHROOT_BASE_DIR firecracker * $KERNEL_IMAGE_PATH * $JAIL_UID $JAIL_GID
EOF
if ! visudo -c -f "${STAGE_RULE_FILE}.tmp" >/dev/null; then
    echo "error: generated sudoers rule failed visudo validation -- not installed" >&2
    rm -f "${STAGE_RULE_FILE}.tmp"
    exit 1
fi
mv "${STAGE_RULE_FILE}.tmp" "$STAGE_RULE_FILE"
chmod 440 "$STAGE_RULE_FILE"
echo "    installed: $STAGE_RULE_FILE"

echo "==> done. Verify general sudo still requires a password for anything else:"
echo "    sudo -k && sudo whoami   # as $INVOKING_USER, should prompt"
echo ""
echo "FirecrackerSandbox(..., jail_uid=$JAIL_UID, jail_gid=$JAIL_GID, jailer_binary=\"$JAILER_BINARY\", chroot_base_dir=\"$CHROOT_BASE_DIR\", stage_script=\"$STAGE_SCRIPT\") is now ready to use real jailer hardening (cold-boot only -- see this script's own header for why snapshot/restore + jailer is deliberately not attempted yet)."
