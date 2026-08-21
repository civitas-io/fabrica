"""Errors for Sandbox and SandboxPool -- see docs/contracts/sandbox.md."""

from __future__ import annotations


class SandboxError(Exception):
    """Base for all Sandbox/SandboxPool errors."""


class SandboxTimeoutError(SandboxError):
    """Code did not complete within the given timeout. The instance is
    killed; the handle is no longer usable after this -- callers must not
    run() with the same handle.
    """


class SandboxToolCallTimeoutError(SandboxTimeoutError):
    """A single on_tool_call invocation did not complete within
    tool_call_timeout, distinct from run()'s overall timeout -- closes
    contracts/sandbox.md's open item 3 ("a single slow tool call could
    otherwise consume the whole budget silently"). A subclass of
    SandboxTimeoutError, not a sibling: the consequence is identical (the
    instance is killed, the handle is no longer usable), so an existing
    `except SandboxTimeoutError` handler still catches this unchanged --
    only a caller that wants to distinguish "one bad tool implementation"
    from "the sandboxed code itself ran long" needs to catch this
    specifically.
    """


class SandboxCrashedError(SandboxError):
    """The instance died unexpectedly during run() -- not a timeout, not a
    code-level exception. Handle is no longer usable. Matches
    system-design.md §6's "Sandbox crashes mid-run" row: SandboxPool
    discards the handle; Civitas supervision (a separate concern) is what
    actually restarts the underlying supervised process, if this pool is
    running in service mode.
    """


class SandboxConfigurationError(SandboxError):
    """An invalid combination of constructor arguments was given -- raised
    at construction time, before any real resource (process, socket,
    file) is ever touched. First real use: FirecrackerSandbox rejects
    `use_jailer=True` combined with `use_snapshot_restore=True` --
    deliberately never validated (a real, separate, harder combination on
    top of two already-separately-validated ones), per direct user
    decision ("security over optimization", cold-boot only for jailer
    support) -- see specs/archive/spikes/
    SPIKE-firecracker-jailer-vsock-integration.md.
    """


class SandboxPoolExhaustedError(SandboxError):
    """acquire() found no handle within acquire_timeout -- both the warm
    pool and cold-start-up-to-max_concurrent were unavailable. Structured
    error, not a hang, per system-design.md §6's resolution of this exact
    failure mode.
    """
