"""CivitasRuntime -- see docs/contracts/civitas-bridge.md.

CivitasRuntime/StateStore themselves are structural Protocols -- Fabrica
does not require a real `civitas.runtime.Runtime` instance specifically,
only something satisfying this shape, matching the "wrap, don't build;
depend on shapes, not packages" pattern used for PresidiumClient/
Summarizer. The one deliberate exception is `GenServer` below: Civitas's
real dynamic-spawn mechanism is nominally coupled to that concrete class,
not just its structural shape, so `civitas` IS a real runtime dependency
of this specific module (see the inline note on the GenServer import) --
consistent with `CivitasBridge` being the one component architecture.md
§1a licenses to integrate tightly with Civitas.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from civitas.genserver import GenServer

# GenServer is a REAL import from civitas, not a structural Protocol like
# CivitasRuntime/StateStore below -- deliberately, not an inconsistency.
# An earlier pass of this module tried a fabrica-defined CivitasGenServer
# Protocol (structural, name-only __init__) here instead, matching this
# file's own "depend on shapes, not packages" framing. mypy caught a real
# bug in that attempt: civitas.runtime.Runtime.spawn's actual parameter
# type is `type[AgentProcess]`, a real Civitas class with a much richer,
# nominally-coupled shape (bus/registry/capability wiring set as instance
# attributes post-construction) than any small structural Protocol could
# honestly capture -- Civitas's real dynamic-spawn mechanism is
# fundamentally nominal here, not structural. Importing the real
# civitas.genserver.GenServer type is the honest fix, not a workaround --
# and it's consistent with architecture.md §1a's own scope note that
# CivitasBridge (unique among Fabrica's components) is explicitly
# licensed to integrate tightly with Civitas. This is also exactly what
# contracts/civitas-bridge.md's own `spawn` signature already said
# (`agent_class: type[GenServer]`) before this module's implementation
# briefly drifted from it.


@runtime_checkable
class CivitasRuntime(Protocol):
    """The subset of civitas.runtime.Runtime's real public API CivitasBridge
    needs. Not a new interface Civitas must conform to -- civitas.runtime.Runtime
    already satisfies this shape today.
    """

    async def spawn(
        self,
        supervisor_name: str,
        agent_class: type[GenServer],
        name: str,
        config: dict[str, Any] | None = None,
        *,
        wait: bool = True,
    ) -> str: ...

    async def despawn(self, supervisor_name: str, name: str) -> None: ...

    def get_agent(self, name: str) -> object | None: ...

    # ^ Real addition, closing contracts/civitas-bridge.md's open item 1
    # ("validate dynamic_supervisor_name upfront, or let the first spawn()
    # surface it"): civitas.runtime.Runtime.get_agent() is real, public,
    # O(1) API -- "return type object, not a specific class" is
    # deliberate, matching this Protocol's own structural-typing stance;
    # CivitasBridge only ever checks the result against None, never
    # inspects it further.


# No SpawnError class is defined here. Civitas's real spawn() raises
# civitas.errors.SpawnError (an earlier draft of contracts/civitas-bridge.md
# guessed civitas.process -- fixed after reading the real source, not left
# wrong). CivitasBridge.request_supervision does not catch or re-wrap it --
# it propagates whatever exception type the injected CivitasRuntime actually
# raises, unchanged, exactly as contracts/civitas-bridge.md's docstring
# says. Defining a separate fabrica.civitas_bridge.SpawnError class here
# would be dead code: nothing in this module would ever construct or raise
# it, and it would invite callers to `except fabrica...SpawnError` and have
# that silently never match the real civitas.errors.SpawnError instance
# that's actually thrown -- a strictly worse outcome than not defining it.
