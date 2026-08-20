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
from fabrica.sandbox.network_policy import NetworkPolicy
from fabrica.sandbox.srt_backend import SrtSandbox, srt_available
from fabrica.sandbox.subprocess_backend import SubprocessSandbox

_FC_BINARY_ENV = "FABRICA_FC_BINARY"
_FC_KERNEL_ENV = "FABRICA_FC_KERNEL"
_FC_ROOTFS_ENV = "FABRICA_FC_ROOTFS"


def select_sandbox_backend(network_policy: NetworkPolicy | None = None) -> Sandbox:
    """The best tier THIS host can prove it can run -- not the best tier
    isolation.md's table describes in the abstract. Three real outcomes
    exist today, stated honestly:

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
      fallback exists when they're absent, unlike those two.
    - SrtSandbox (Tier 1), when the `srt` binary is present on PATH and
      Firecracker wasn't selected -- covers macOS and Linux-without-KVM
      today (verified live on macOS only so far; srt documents Linux/
      Windows support, not yet exercised here -- see srt_backend.py's
      own honesty note). `network_policy` defaults to
      NetworkPolicy() (empty allowlist = deny all network) if the
      caller doesn't pass one -- a caller that forgets to pass a real
      scope-derived policy gets a sandbox with NO network access at
      all, never an accidentally-open one.
    - SubprocessSandbox (Tier 0) otherwise -- the last resort when
      neither Firecracker nor `srt` is available. Provides NO network
      or filesystem isolation (see its own module docstring) --
      selecting this path for anything a scope document must bound is
      a real gap, not a acceptable degraded mode; callers should treat
      Tier 0 selection here as a loud signal, not a quiet fallback.
    """
    if _firecracker_available():
        return FirecrackerSandbox(
            firecracker_binary=os.environ[_FC_BINARY_ENV],
            kernel_image_path=os.environ[_FC_KERNEL_ENV],
            base_rootfs_path=os.environ[_FC_ROOTFS_ENV],
        )
    if srt_available():
        return SrtSandbox(network_policy or NetworkPolicy())
    return SubprocessSandbox()


def _firecracker_available() -> bool:
    if platform.system() != "Linux" or not Path("/dev/kvm").exists():
        return False
    binary = os.environ.get(_FC_BINARY_ENV, "")
    kernel = os.environ.get(_FC_KERNEL_ENV, "")
    rootfs = os.environ.get(_FC_ROOTFS_ENV, "")
    return bool(binary and kernel and rootfs and Path(kernel).exists() and Path(rootfs).exists())
