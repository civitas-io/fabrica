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


class SupervisorNotFoundError(CivitasBridgeError):
    """Raised at construction time (not at the first request_supervision()
    call) when dynamic_supervisor_name does not resolve to any live agent
    on the given civitas_runtime -- contracts/civitas-bridge.md's open item
    1, resolved: validate upfront via the real, public
    civitas.runtime.Runtime.get_agent() lookup, since a real, clear error
    now beats waiting for spawn()'s bus-routing failure to surface the
    same misconfiguration less specifically, later, on first real use.

    Deliberately does NOT check that the resolved agent is specifically a
    DynamicSupervisor (as opposed to any other named agent) -- existence
    is what makes the error message clearer and earlier; the exact-type
    check would need importing civitas.supervisor.DynamicSupervisor as a
    second nominal exception beyond GenServer, for marginal benefit over
    what spawn()'s own SpawnError would surface if the name resolves to
    the wrong kind of agent.
    """
