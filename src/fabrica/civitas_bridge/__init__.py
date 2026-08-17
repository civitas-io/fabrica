"""CivitasBridge -- the object-graph assembler. See docs/contracts/civitas-bridge.md.

Sixth and final object-model contract. The one component architecture.md
§1a grants permission to integrate tightly with Civitas -- everything else
in fabrica/ stays a usable, independent library.
"""

from fabrica.civitas_bridge.bridge import CivitasBridge, Fabrica, NullPresidiumClient
from fabrica.civitas_bridge.errors import (
    CivitasBridgeError,
    RuntimeRequiredError,
    SupervisorNotFoundError,
    UngovernedConfigurationError,
)
from fabrica.civitas_bridge.runtime import CivitasRuntime
from fabrica.civitas_bridge.state import ComponentStateHandle, StateStore

__all__ = [
    "CivitasBridge",
    "CivitasBridgeError",
    "CivitasRuntime",
    "ComponentStateHandle",
    "Fabrica",
    "NullPresidiumClient",
    "RuntimeRequiredError",
    "StateStore",
    "SupervisorNotFoundError",
    "UngovernedConfigurationError",
]
