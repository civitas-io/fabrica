"""Errors for Sandbox and SandboxPool -- see docs/contracts/sandbox.md."""

from __future__ import annotations


class SandboxError(Exception):
    """Base for all Sandbox/SandboxPool errors."""


class SandboxTimeoutError(SandboxError):
    """Code did not complete within the given timeout. The instance is
    killed; the handle is no longer usable after this -- callers must not
    run() with the same handle.
    """


class SandboxCrashedError(SandboxError):
    """The instance died unexpectedly during run() -- not a timeout, not a
    code-level exception. Handle is no longer usable. Matches
    system-design.md §6's "Sandbox crashes mid-run" row: SandboxPool
    discards the handle; Civitas supervision (a separate concern) is what
    actually restarts the underlying supervised process, if this pool is
    running in service mode.
    """


class SandboxPoolExhaustedError(SandboxError):
    """acquire() found no handle within acquire_timeout -- both the warm
    pool and cold-start-up-to-max_concurrent were unavailable. Structured
    error, not a hang, per system-design.md §6's resolution of this exact
    failure mode.
    """
