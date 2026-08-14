"""CivitasBridge -- see docs/contracts/civitas-bridge.md.

Real civitas.runtime.Runtime/DynamicSupervisor/InMemoryStateStore are used
throughout, not hand-rolled test doubles -- civitas is a real, local,
PyPI-installable dependency, per HANDOFF.md's resolution of the
CivitasBridge build-path decision.
"""

from __future__ import annotations

import pytest
from civitas import DynamicSupervisor, GenServer, Runtime, Supervisor
from civitas.errors import SpawnError as RealSpawnError
from civitas.plugins.state import InMemoryStateStore

from fabrica.civitas_bridge import (
    CivitasBridge,
    Fabrica,
    NullPresidiumClient,
    RuntimeRequiredError,
    UngovernedConfigurationError,
)
from fabrica.managers import SkillManager, ToolManager
from fabrica.memory import MemoryManager
from fabrica.memory.types import MemoryItem
from fabrica.memory.types import Message as FabricaMessage
from fabrica.presidium import GrantResult
from fabrica.prompts import PromptManager
from fabrica.scope import Scope


class _EchoWorker(GenServer):
    """A minimal, genuinely fresh, self-contained agent -- the shape
    request_supervision is actually designed for (type[GenServer], per
    both the contract and civitas.runtime.Runtime.spawn's real signature),
    NOT one of Fabrica's own DI-constructed managers. Never sent a real
    message in these tests -- GenServer's default handle_call/handle_cast/
    handle_info are exercised only if something calls it, which nothing
    here does.
    """


class _FakeSummarizer:
    async def summarize(self, messages: list[FabricaMessage], *, target_tokens: int) -> str:
        return " ".join(m.content for m in messages)[:target_tokens]


class _DenyingPresidiumClient:
    async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
        return GrantResult(decision="deny", reason="test double, always denies")


# ---------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_no_presidium_client_and_not_allow_ungoverned_raises(self) -> None:
        with pytest.raises(UngovernedConfigurationError):
            CivitasBridge()

    def test_no_presidium_client_with_allow_ungoverned_succeeds(self) -> None:
        CivitasBridge(allow_ungoverned=True)  # must not raise

    def test_real_presidium_client_without_allow_ungoverned_succeeds(self) -> None:
        CivitasBridge(presidium_client=_DenyingPresidiumClient())  # must not raise

    def test_service_mode_missing_all_three_raises(self) -> None:
        with pytest.raises(RuntimeRequiredError):
            CivitasBridge(mode="service", allow_ungoverned=True)

    @pytest.mark.parametrize("missing", ["runtime", "state_store", "supervisor_name"])
    async def test_service_mode_missing_one_of_three_raises(self, missing: str) -> None:
        runtime = Runtime(supervisor=Supervisor("root", children=[DynamicSupervisor("dyn")]))
        await runtime.start()
        try:
            kwargs: dict[str, object] = {
                "civitas_runtime": None if missing == "runtime" else runtime,
                "civitas_state_store": None if missing == "state_store" else InMemoryStateStore(),
                "dynamic_supervisor_name": None if missing == "supervisor_name" else "dyn",
            }
            with pytest.raises(RuntimeRequiredError):
                CivitasBridge(mode="service", allow_ungoverned=True, **kwargs)  # type: ignore[arg-type]
        finally:
            await runtime.stop()

    async def test_service_mode_all_three_present_succeeds(self) -> None:
        runtime = Runtime(supervisor=Supervisor("root", children=[DynamicSupervisor("dyn")]))
        await runtime.start()
        try:
            CivitasBridge(
                mode="service",
                allow_ungoverned=True,
                civitas_runtime=runtime,
                civitas_state_store=InMemoryStateStore(),
                dynamic_supervisor_name="dyn",
            )  # must not raise
        finally:
            await runtime.stop()


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


class TestBuild:
    async def test_returns_fabrica_with_all_four_managers(self) -> None:
        bridge = CivitasBridge(allow_ungoverned=True)
        fabrica = await bridge.build()
        assert isinstance(fabrica, Fabrica)
        assert isinstance(fabrica.tools, ToolManager)
        assert isinstance(fabrica.skills, SkillManager)
        assert isinstance(fabrica.memory, MemoryManager)
        assert isinstance(fabrica.prompts, PromptManager)

    async def test_close_terminates_the_sandbox_pools_warm_instances(self) -> None:
        """A real gap found by testing SandboxPool wrapped around
        FirecrackerSandbox rather than only the fast in-memory test
        double: nothing terminated warm-pool instances at shutdown, and
        there was no discoverable way to do so from a built Fabrica at
        all. sandbox_pool is now a public field specifically so this is
        possible and easy to find.
        """
        fabrica = await CivitasBridge(allow_ungoverned=True, warm_size=2).build()
        await fabrica.sandbox_pool.prewarm()

        await fabrica.close()  # must not raise

        assert fabrica.sandbox_pool.warm_count == 0

    async def test_no_summarizer_wires_null_compactor(self) -> None:
        fabrica = await CivitasBridge(allow_ungoverned=True).build()
        # NullCompactor raises on the first real compact() call --
        # RecencyCompactor would not, proving which one got wired in
        # without reaching into MemoryManager's private state.
        from fabrica.memory import CompactionUnavailableError

        with pytest.raises(CompactionUnavailableError):
            await fabrica.memory.compact([], budget_tokens=100)

    async def test_summarizer_wires_recency_compactor(self) -> None:
        bridge = CivitasBridge(allow_ungoverned=True, summarizer=_FakeSummarizer())
        fabrica = await bridge.build()
        result = await fabrica.memory.compact([], budget_tokens=100)
        # RecencyCompactor handles the empty-history case without raising --
        # a NullCompactor would have raised instead (see the test above).
        assert result is not None

    async def test_no_presidium_client_wires_null_presidium_client_that_allows(self) -> None:
        fabrica = await CivitasBridge(allow_ungoverned=True).build()
        code = "result = 1 + 1"
        run_result = await fabrica.tools.run_code(
            code, agent_id="agent-1", scope=Scope(agent_id="agent-1")
        )
        # A denying PresidiumClient would raise GrantDeniedError before
        # anything runs -- reaching a real RunResult proves check_grant
        # allowed the call.
        assert run_result is not None

    async def test_real_presidium_client_that_denies_blocks_run_code(self) -> None:
        from fabrica.managers import GrantDeniedError

        fabrica = await CivitasBridge(presidium_client=_DenyingPresidiumClient()).build()
        with pytest.raises(GrantDeniedError):
            await fabrica.tools.run_code(
                "result = 1", agent_id="agent-1", scope=Scope(agent_id="agent-1")
            )

    async def test_repeated_build_calls_return_independent_fabrica_instances(self) -> None:
        # Open item 2 (contracts/civitas-bridge.md): idempotency is not
        # contract-specified. This documents the actual current behavior
        # (a fresh, independent graph each call) as a real test, not a
        # silent assumption.
        bridge = CivitasBridge(allow_ungoverned=True)
        first = await bridge.build()
        second = await bridge.build()
        assert first is not second
        assert first.tools is not second.tools


# ---------------------------------------------------------------------------
# Service-mode persistence -- build() wires PersistedMemoryStore/
# PersistedPromptStore over a real civitas.plugins.state.StateStore
# ---------------------------------------------------------------------------


class TestServiceModePersistence:
    def _service_mode_bridge(
        self, state_store: InMemoryStateStore, runtime: Runtime
    ) -> CivitasBridge:
        return CivitasBridge(
            mode="service",
            allow_ungoverned=True,
            civitas_runtime=runtime,
            civitas_state_store=state_store,
            dynamic_supervisor_name="dyn",
        )

    async def test_memory_written_in_one_build_survives_a_second_build_over_the_same_store(
        self,
    ) -> None:
        state_store = InMemoryStateStore()
        runtime = Runtime(supervisor=Supervisor("root", children=[DynamicSupervisor("dyn")]))
        await runtime.start()
        try:
            first_fabrica = await self._service_mode_bridge(state_store, runtime).build()
            await first_fabrica.memory.write(
                Scope(agent_id="a1"), MemoryItem(id=None, content="survives a restart")
            )

            # A SECOND, independent build() over the SAME state_store --
            # simulates a process restart, not just a second in-process call.
            second_fabrica = await self._service_mode_bridge(state_store, runtime).build()
            results = await second_fabrica.memory.search(Scope(agent_id="a1"), "survives")
            assert len(results) == 1
            assert results[0].content == "survives a restart"
        finally:
            await runtime.stop()

    async def test_prompts_written_in_one_build_survive_a_second_build_over_the_same_store(
        self,
    ) -> None:
        state_store = InMemoryStateStore()
        runtime = Runtime(supervisor=Supervisor("root", children=[DynamicSupervisor("dyn")]))
        await runtime.start()
        try:
            first_fabrica = await self._service_mode_bridge(state_store, runtime).build()
            await first_fabrica.prompts.put("greeting", "Hello!")

            second_fabrica = await self._service_mode_bridge(state_store, runtime).build()
            template = await second_fabrica.prompts.get("greeting")
            assert template is not None
            assert template.content == "Hello!"
        finally:
            await runtime.stop()

    async def test_library_mode_never_persists_across_separate_builds(self) -> None:
        # Contrast case -- proves library mode's in-memory defaults are
        # NOT accidentally shared/persisted anywhere, unlike service mode.
        first_fabrica = await CivitasBridge(allow_ungoverned=True).build()
        await first_fabrica.memory.write(Scope(agent_id="a1"), MemoryItem(id=None, content="x"))

        second_fabrica = await CivitasBridge(allow_ungoverned=True).build()
        results = await second_fabrica.memory.search(Scope(agent_id="a1"), "x")
        assert results == []


# ---------------------------------------------------------------------------
# request_supervision -- real Civitas Runtime/DynamicSupervisor
# ---------------------------------------------------------------------------


class TestRequestSupervision:
    async def test_spawns_a_real_agent_and_returns_its_name(self) -> None:
        runtime = Runtime(supervisor=Supervisor("root", children=[DynamicSupervisor("dyn")]))
        await runtime.start()
        try:
            bridge = CivitasBridge(
                mode="service",
                allow_ungoverned=True,
                civitas_runtime=runtime,
                civitas_state_store=InMemoryStateStore(),
                dynamic_supervisor_name="dyn",
            )
            name = await bridge.request_supervision(_EchoWorker, "echo-1")
            assert name == "echo-1"
        finally:
            await runtime.stop()

    async def test_name_collision_raises_real_spawn_error_unwrapped(self) -> None:
        runtime = Runtime(supervisor=Supervisor("root", children=[DynamicSupervisor("dyn")]))
        await runtime.start()
        try:
            bridge = CivitasBridge(
                mode="service",
                allow_ungoverned=True,
                civitas_runtime=runtime,
                civitas_state_store=InMemoryStateStore(),
                dynamic_supervisor_name="dyn",
            )
            await bridge.request_supervision(_EchoWorker, "dup")
            # civitas.runtime.Runtime.spawn raises civitas.errors.SpawnError
            # directly -- request_supervision must not catch or re-wrap it,
            # per the contract's "propagated unchanged" note.
            with pytest.raises(RealSpawnError):
                await bridge.request_supervision(_EchoWorker, "dup")
        finally:
            await runtime.stop()

    async def test_library_mode_raises_runtime_required(self) -> None:
        bridge = CivitasBridge(allow_ungoverned=True)
        with pytest.raises(RuntimeRequiredError):
            await bridge.request_supervision(_EchoWorker, "echo-1")


# ---------------------------------------------------------------------------
# request_state_persistence -- real civitas.plugins.state.StateStore
# ---------------------------------------------------------------------------


class TestRequestStatePersistence:
    async def test_handle_is_bound_to_its_own_component_name(self) -> None:
        runtime = Runtime(supervisor=Supervisor("root", children=[DynamicSupervisor("dyn")]))
        await runtime.start()
        try:
            store = InMemoryStateStore()
            bridge = CivitasBridge(
                mode="service",
                allow_ungoverned=True,
                civitas_runtime=runtime,
                civitas_state_store=store,
                dynamic_supervisor_name="dyn",
            )
            memory_handle = await bridge.request_state_persistence("memory_manager")
            prompts_handle = await bridge.request_state_persistence("prompts_manager")

            await memory_handle.set({"key": "memory-value"})
            await prompts_handle.set({"key": "prompts-value"})

            assert await memory_handle.get() == {"key": "memory-value"}
            assert await prompts_handle.get() == {"key": "prompts-value"}
            # Proves real name-binding against the shared underlying store,
            # not just two independent in-memory dicts.
            assert await store.get("memory_manager") == {"key": "memory-value"}
            assert await store.get("prompts_manager") == {"key": "prompts-value"}
        finally:
            await runtime.stop()

    async def test_delete_only_affects_its_own_component(self) -> None:
        store = InMemoryStateStore()
        bridge = CivitasBridge(
            mode="service",
            allow_ungoverned=True,
            civitas_runtime=Runtime(supervisor=Supervisor("root", children=[])),
            civitas_state_store=store,
            dynamic_supervisor_name="dyn",
        )
        a = await bridge.request_state_persistence("a")
        b = await bridge.request_state_persistence("b")
        await a.set({"v": 1})
        await b.set({"v": 2})
        await a.delete()
        assert await a.get() is None
        assert await b.get() == {"v": 2}

    async def test_library_mode_raises_runtime_required(self) -> None:
        bridge = CivitasBridge(allow_ungoverned=True)
        with pytest.raises(RuntimeRequiredError):
            await bridge.request_state_persistence("memory_manager")


def test_null_presidium_client_always_allows() -> None:
    import asyncio

    client = NullPresidiumClient()
    result = asyncio.run(
        client.check_grant(agent_id="a", action="code_mode", scope=Scope(agent_id="a"))
    )
    assert result.decision == "allow"
