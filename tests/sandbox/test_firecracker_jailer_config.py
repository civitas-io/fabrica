"""Pure, hardware-independent tests for FirecrackerSandbox's use_jailer=True
configuration surface -- construction-time guards and health_check()'s
artifact-existence checks. Deliberately a SEPARATE file from
test_firecracker_backend.py: that file's module-level `pytestmark` skips
every test unless real Firecracker + KVM + jailer infra is present, which
would have silently skipped these too even though none of them boot a real
VM or need real hardware at all -- found and fixed during this same
implementation, not a pre-existing convention.

See specs/archive/spikes/SPIKE-firecracker-jailer-vsock-integration.md for
the full, empirically-validated mechanism these guards protect.
"""

from __future__ import annotations

import pytest

from fabrica.sandbox.errors import SandboxConfigurationError
from fabrica.sandbox.firecracker_backend import FirecrackerSandbox


def test_use_jailer_and_use_snapshot_restore_together_raises_at_construction() -> None:
    # Deliberately never validated: a real, separate, harder combination on
    # top of two already-separately-validated ones, per direct user
    # decision ("security over optimization" -- jailer support is
    # cold-boot only).
    with pytest.raises(SandboxConfigurationError, match="cannot be combined"):
        FirecrackerSandbox(
            firecracker_binary="/bin/true",
            kernel_image_path="/no/such/kernel",
            base_rootfs_path="/no/such/rootfs",
            use_jailer=True,
            use_snapshot_restore=True,
            jailer_binary="/bin/true",
            jail_uid=1,
            jail_gid=1,
            chroot_base_dir="/tmp",
            stage_script="/bin/true",
        )


def test_use_jailer_without_required_args_raises_at_construction() -> None:
    with pytest.raises(SandboxConfigurationError, match="requires"):
        FirecrackerSandbox(
            firecracker_binary="/bin/true",
            kernel_image_path="/no/such/kernel",
            base_rootfs_path="/no/such/rootfs",
            use_jailer=True,
        )


def test_use_jailer_false_does_not_require_jailer_args() -> None:
    # The default -- must not raise even though every jailer-specific arg
    # is left at its own empty/zero default. No behavior change for any
    # existing caller.
    FirecrackerSandbox(
        firecracker_binary="/bin/true",
        kernel_image_path="/no/such/kernel",
        base_rootfs_path="/no/such/rootfs",
    )


async def test_jailer_health_check_false_when_jailer_binary_missing() -> None:
    # Constructor requires jailer_binary to be non-empty, but health_check
    # must still report unhealthy for a path that doesn't actually exist.
    backend = FirecrackerSandbox(
        firecracker_binary="/bin/true",
        kernel_image_path="/no/such/kernel",
        base_rootfs_path="/no/such/rootfs",
        use_jailer=True,
        jailer_binary="/no/such/jailer",
        jail_uid=1,
        jail_gid=1,
        chroot_base_dir="/tmp",
        stage_script="/bin/true",
    )
    assert await backend.health_check() is False


async def test_jailer_health_check_false_when_stage_script_missing() -> None:
    backend = FirecrackerSandbox(
        firecracker_binary="/bin/true",
        kernel_image_path="/no/such/kernel",
        base_rootfs_path="/no/such/rootfs",
        use_jailer=True,
        jailer_binary="/bin/true",
        jail_uid=1,
        jail_gid=1,
        chroot_base_dir="/tmp",
        stage_script="/no/such/stage/script.sh",
    )
    assert await backend.health_check() is False
