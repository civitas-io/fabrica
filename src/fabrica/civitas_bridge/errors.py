"""CivitasBridge's own errors -- see docs/contracts/civitas-bridge.md."""

from __future__ import annotations


class CivitasBridgeError(Exception):
    """Base for CivitasBridge-specific errors."""


class UngovernedConfigurationError(CivitasBridgeError):
    """Raised at construction time (not at first check_grant call) when
    presidium_client is None and allow_ungoverned is False -- the default
    combination. Forces an explicit decision before Fabrica is usable at
    all, rather than allowing "nobody configured this" to silently become
    "everything is allowed" by omission.
    """


class RuntimeRequiredError(CivitasBridgeError):
    """Raised at construction time when mode="service" but civitas_runtime,
    civitas_state_store, or dynamic_supervisor_name is None -- service mode
    is meaningless without a runtime and a named supervisor to spawn into,
    and state persistence needs the real StateStore. All three required
    together, per CivitasBridge.__init__'s Raises section.
    """
