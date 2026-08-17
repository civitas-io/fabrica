"""Platform dispatch for the Sandbox backend -- see docs/isolation.md
("Platform dispatch -- auto-detected, not user-configured").

CivitasBridge.build() calls select_sandbox_backend() with no arguments in
every real deployment. It is not a general per-deployment config knob --
isolation.md is explicit that the backend choice is not user-facing.
CivitasBridge.__init__'s own sandbox_backend override exists ONLY for the
"hidden override for testing/CI" isolation.md names, not for this.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from fabrica.sandbox.backend import Sandbox
from fabrica.sandbox.firecracker_backend import FirecrackerSandbox
from fabrica.sandbox.subprocess_backend import SubprocessSandbox

_FC_BINARY_ENV = "FABRICA_FC_BINARY"
_FC_KERNEL_ENV = "FABRICA_FC_KERNEL"
_FC_ROOTFS_ENV = "FABRICA_FC_ROOTFS"


def select_sandbox_backend() -> Sandbox:
    """The best tier THIS host can prove it can run -- not the best tier
    isolation.md's table describes in the abstract. Only two real
    outcomes exist today, stated honestly:

    - FirecrackerSandbox (Tier 2), when ALL of: running on Linux, real
      KVM present (/dev/kvm exists), and real deployment-specific
      artifacts are configured via FABRICA_FC_BINARY/FABRICA_FC_KERNEL/
      FABRICA_FC_ROOTFS, pointing at files that actually exist. These
      are read from the environment (not this project's general config
      pattern) because they are real per-deployment binary/kernel/rootfs
      paths with no sensible auto-discovered default -- the same "a real
      external dependency, not invented" shape as PresidiumClient's and
      Summarizer's constructor-injection, adapted for a factory function
      rather than a required injected object because a real, safe
      fallback (Tier 0) exists when they're absent, unlike those two.
    - SubprocessSandbox (Tier 0) otherwise -- covers macOS, Windows,
      Linux without KVM, and Linux without the Firecracker artifacts
      configured. Tier 1 (gVisor/srt) and macOS/Windows Tier 2
      (libkrun/Hyper-V) remain unimplemented (contracts/sandbox.md),
      so there is no third real outcome yet -- adding one later is
      additive to this function, not a rework of it.
    """
    if _firecracker_available():
        return FirecrackerSandbox(
            firecracker_binary=os.environ[_FC_BINARY_ENV],
            kernel_image_path=os.environ[_FC_KERNEL_ENV],
            base_rootfs_path=os.environ[_FC_ROOTFS_ENV],
        )
    return SubprocessSandbox()


def _firecracker_available() -> bool:
    if platform.system() != "Linux" or not Path("/dev/kvm").exists():
        return False
    binary = os.environ.get(_FC_BINARY_ENV, "")
    kernel = os.environ.get(_FC_KERNEL_ENV, "")
    rootfs = os.environ.get(_FC_ROOTFS_ENV, "")
    return bool(binary and kernel and rootfs and Path(kernel).exists() and Path(rootfs).exists())
