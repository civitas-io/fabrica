"""Tests for select_sandbox_backend() -- real platform dispatch, per
docs/isolation.md. Deterministic: monkeypatches platform.system(),
Path.exists(), and the FABRICA_FC_* env vars rather than depending on
whatever this test machine actually has -- the real end-to-end proof
that dispatch picks FirecrackerSandbox for real, on real Linux+KVM+
Firecracker, lives in test_pool_with_firecracker.py / HANDOFF.md instead.
"""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

from fabrica.sandbox import FirecrackerSandbox, SubprocessSandbox, select_sandbox_backend


def _clear_fc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FABRICA_FC_BINARY", "FABRICA_FC_KERNEL", "FABRICA_FC_ROOTFS"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_to_subprocess_sandbox_when_env_vars_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_fc_env(monkeypatch)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "exists", lambda self: True)  # even /dev/kvm "present"

    backend = select_sandbox_backend()

    assert isinstance(backend, SubprocessSandbox)


def test_defaults_to_subprocess_sandbox_on_macos_even_with_full_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kernel = tmp_path / "kernel"
    rootfs = tmp_path / "rootfs.ext4"
    kernel.touch()
    rootfs.touch()
    monkeypatch.setenv("FABRICA_FC_BINARY", "/usr/bin/firecracker")
    monkeypatch.setenv("FABRICA_FC_KERNEL", str(kernel))
    monkeypatch.setenv("FABRICA_FC_ROOTFS", str(rootfs))
    monkeypatch.setattr(platform, "system", lambda: "Darwin")

    backend = select_sandbox_backend()

    assert isinstance(backend, SubprocessSandbox)


def test_defaults_to_subprocess_sandbox_on_linux_without_kvm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kernel = tmp_path / "kernel"
    rootfs = tmp_path / "rootfs.ext4"
    kernel.touch()
    rootfs.touch()
    monkeypatch.setenv("FABRICA_FC_BINARY", "/usr/bin/firecracker")
    monkeypatch.setenv("FABRICA_FC_KERNEL", str(kernel))
    monkeypatch.setenv("FABRICA_FC_ROOTFS", str(rootfs))
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    real_exists = Path.exists

    def _fake_exists(self: Path) -> bool:
        if str(self) == "/dev/kvm":
            return False
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _fake_exists)

    backend = select_sandbox_backend()

    assert isinstance(backend, SubprocessSandbox)


def test_defaults_to_subprocess_sandbox_when_configured_kernel_path_does_not_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rootfs = tmp_path / "rootfs.ext4"
    rootfs.touch()
    monkeypatch.setenv("FABRICA_FC_BINARY", "/usr/bin/firecracker")
    monkeypatch.setenv("FABRICA_FC_KERNEL", str(tmp_path / "no-such-kernel"))
    monkeypatch.setenv("FABRICA_FC_ROOTFS", str(rootfs))
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    real_exists = Path.exists

    def _fake_exists(self: Path) -> bool:
        if str(self) == "/dev/kvm":
            return True
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _fake_exists)

    backend = select_sandbox_backend()

    assert isinstance(backend, SubprocessSandbox)


def test_selects_firecracker_sandbox_when_linux_kvm_and_real_artifacts_all_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kernel = tmp_path / "kernel"
    rootfs = tmp_path / "rootfs.ext4"
    kernel.touch()
    rootfs.touch()
    monkeypatch.setenv("FABRICA_FC_BINARY", "/usr/bin/firecracker")
    monkeypatch.setenv("FABRICA_FC_KERNEL", str(kernel))
    monkeypatch.setenv("FABRICA_FC_ROOTFS", str(rootfs))
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    real_exists = Path.exists

    def _fake_exists(self: Path) -> bool:
        if str(self) == "/dev/kvm":
            return True
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _fake_exists)

    backend = select_sandbox_backend()

    assert isinstance(backend, FirecrackerSandbox)
