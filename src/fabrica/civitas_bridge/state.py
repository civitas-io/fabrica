"""ComponentStateHandle -- see docs/contracts/civitas-bridge.md.

StateStore is defined here as a structural Protocol matching
civitas.plugins.state.StateStore's real shape (confirmed by reading
civitas/plugins/state.py directly: get(agent_name)/set(agent_name, state)/
delete(agent_name)/list_agents()/close()) -- not imported from `civitas`,
same "depend on shapes, not packages" pattern as CivitasRuntime.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StateStore(Protocol):
    """The subset of civitas.plugins.state.StateStore's real API
    CivitasBridge needs. civitas.plugins.state.StateStore (and its
    InMemoryStateStore default) already satisfy this shape today.
    """

    async def get(self, agent_name: str) -> dict[str, Any] | None: ...
    async def set(self, agent_name: str, state: dict[str, Any]) -> None: ...
    async def delete(self, agent_name: str) -> None: ...


@runtime_checkable
class ComponentStateHandle(Protocol):
    """A StateStore access already bound to one component's name -- so a
    manager cannot accidentally read or write a different component's
    state by passing the wrong key. CivitasBridge is the only thing
    holding a direct reference to the raw StateStore; managers only ever
    see this name-bound wrapper.
    """

    async def get(self) -> dict[str, Any] | None: ...
    async def set(self, state: dict[str, Any]) -> None: ...
    async def delete(self) -> None: ...


class _BoundStateHandle:
    """The concrete ComponentStateHandle CivitasBridge.request_state_persistence
    returns -- a thin, name-bound wrapper over a real StateStore. Adds no
    logic of its own beyond fixing which agent_name every call uses.
    """

    def __init__(self, store: StateStore, component_name: str) -> None:
        self._store = store
        self._component_name = component_name

    async def get(self) -> dict[str, Any] | None:
        return await self._store.get(self._component_name)

    async def set(self, state: dict[str, Any]) -> None:
        await self._store.set(self._component_name, state)

    async def delete(self) -> None:
        await self._store.delete(self._component_name)
