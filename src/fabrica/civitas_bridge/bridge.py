"""CivitasBridge -- see docs/contracts/civitas-bridge.md.

The one component architecture.md §1a explicitly grants permission to
integrate tightly. Two real gaps found while implementing this against
Civitas's actual API (not just its docs) are documented as corrections in
the contract itself, not papered over here:

1. `request_supervision` is real and tested, but no manager in this
   codebase calls it in v1 -- Civitas's real dynamic-spawn mechanism
   reconstructs an agent class from a dotted path with only `name`, which
   is structurally incompatible with this codebase's constructor-injected
   managers.
2. `request_state_persistence` is real and tested, but `build()` doesn't
   call it for MemoryManager/PromptManager yet either -- no
   StateStore-backed MemoryStore/PromptStore adapter has been designed to
   receive the resulting handle.

Both are named, scoped gaps, not silent shortcuts -- see
"Correction found during implementation" in the contract for the full
reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from civitas.genserver import GenServer

from fabrica.civitas_bridge.errors import (
    RuntimeRequiredError,
    SupervisorNotFoundError,
    UngovernedConfigurationError,
)
from fabrica.civitas_bridge.runtime import CivitasRuntime
from fabrica.civitas_bridge.state import ComponentStateHandle, StateStore, _BoundStateHandle
from fabrica.managers import SkillManager, ToolManager
from fabrica.memory import (
    Compactor,
    InMemoryMemoryStore,
    InMemoryWorkingMemoryStore,
    MemoryManager,
    NullCompactor,
    PersistedMemoryStore,
    RecencyCompactor,
    Summarizer,
)
from fabrica.memory import MemoryStore as MemoryStoreProtocol
from fabrica.observability import NullTracer, Tracer
from fabrica.presidium import GrantResult, PresidiumClient
from fabrica.prompts import InMemoryPromptStore, PersistedPromptStore, PromptManager
from fabrica.prompts import PromptStore as PromptStoreProtocol
from fabrica.retriever import KeywordBackend, Retriever
from fabrica.sandbox import Sandbox, SandboxPool, select_sandbox_backend
from fabrica.scope import Scope

# Not contract-specified (see Open items 6 in contracts/civitas-bridge.md) --
# a reasonable, zero-infra-quickstart-appropriate placeholder, not a tuned
# recommendation for any real workload.
_DEFAULT_WARM_SIZE = 2
_DEFAULT_MAX_CONCURRENT = 4


class NullPresidiumClient:
    """Wired in by CivitasBridge when presidium_client=None and
    allow_ungoverned=True. Implements PresidiumClient's one method,
    unconditionally allowing -- the opposite failure direction from
    NullCompactor (contracts/memory.md), and deliberately so: check_grant
    is mandatory hot-path control flow under code-mode itself (called by
    execute_in_sandbox on every execution), unlike compaction, which a
    harness invokes optionally. Failing closed here by default would make
    the platform's own headline feature unusable without Presidium --
    exactly the zero-infra-quickstart cost this exists to avoid, PROVIDED
    the caller has explicitly opted in via allow_ungoverned=True.
    """

    async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
        return GrantResult(
            decision="allow", reason="allow_ungoverned=True, no Presidium configured"
        )


@dataclass
class Fabrica:
    """The assembled object graph. Every manager is a public attribute --
    never hidden exclusively behind CivitasBridge, per the contract's own
    scope note: this is what makes a later, narrow, opt-in CivitasBridge
    convenience method additive rather than a rework.

    sandbox_pool is exposed directly (not just reachable indirectly
    through tools/skills, which each hold their own private reference to
    the SAME pool) specifically so close() below has something explicit
    to call -- a real gap found by testing SandboxPool wrapped around a
    real backend (FirecrackerSandbox): nothing terminated the warm
    pool's resident instances at shutdown, and there was no discoverable
    way to do so from a constructed Fabrica at all.
    """

    tools: ToolManager
    skills: SkillManager
    memory: MemoryManager
    prompts: PromptManager
    sandbox_pool: SandboxPool

    async def close(self) -> None:
        """Terminate every sandbox instance still resident in the warm
        pool. Must be called once at real deployment shutdown -- without
        it, warm-pool instances are simply abandoned (an easy-to-miss
        orphaned OS process for SubprocessSandbox; a full, impossible-
        to-miss orphaned rootfs copy per warm slot for FirecrackerSandbox,
        which is how this gap was actually found).
        """
        await self.sandbox_pool.close()


class CivitasBridge:
    def __init__(
        self,
        mode: Literal["library", "service"] = "library",
        *,
        summarizer: Summarizer | None = None,
        presidium_client: PresidiumClient | None = None,
        allow_ungoverned: bool = False,
        civitas_runtime: CivitasRuntime | None = None,
        civitas_state_store: StateStore | None = None,
        dynamic_supervisor_name: str | None = None,
        overrides: dict[str, Literal["library", "service"]] | None = None,
        warm_size: int = _DEFAULT_WARM_SIZE,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        sandbox_backend: Sandbox | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        """
        Raises:
            UngovernedConfigurationError: presidium_client is None and
                allow_ungoverned is False.
            RuntimeRequiredError: mode="service" and civitas_runtime,
                civitas_state_store, or dynamic_supervisor_name is None.
                All three are required together in service mode.

        summarizer=None is NOT an error -- MemoryManager receives a
        NullCompactor instead of RecencyCompactor.

        overrides is the v2 per-component mode-granularity hook
        (system-design.md §2) -- present in the signature from v1, unused
        by build() today (uniform mode selection only).

        warm_size/max_concurrent configure the default SandboxPool build()
        constructs -- not contract-specified (Open item 6), reasonable
        zero-infra placeholders.

        sandbox_backend is the "hidden override for testing/CI only"
        isolation.md names -- NOT a general per-deployment config knob.
        Real deployments leave this None and get select_sandbox_backend()'s
        real platform dispatch (isolation.md): FirecrackerSandbox (Tier 2)
        when this host can actually prove it (Linux + real KVM + real
        FABRICA_FC_BINARY/FABRICA_FC_KERNEL/FABRICA_FC_ROOTFS artifacts),
        SubprocessSandbox (Tier 0) otherwise. Supplying a backend directly
        here is for tests that need a specific, deterministic tier without
        depending on the host's real capabilities.

        tracer=None (the default) wires NullTracer() into every
        constructed component -- a real no-op, matching every other DI'd
        dependency in this codebase (PresidiumClient, Summarizer). A real
        civitas.observability.tracer.Tracer() is NOT auto-constructed by
        default here, deliberately: it has real side effects (an OTEL
        SDK TracerProvider, a console exporter printing every span to
        stdout when no OTLP endpoint is configured) that would silently
        change behavior for every existing caller of build(), including
        this codebase's own test suite. Pass a real, fully-constructed
        Tracer explicitly (the same "external dependencies are always
        fully-constructed objects" rule applied everywhere else) to get
        real span emission.
        """
        if presidium_client is None and not allow_ungoverned:
            raise UngovernedConfigurationError(
                "presidium_client is None and allow_ungoverned is False -- "
                "set allow_ungoverned=True to run without Presidium governance, "
                "or supply a real PresidiumClient."
            )
        if mode == "service" and (
            civitas_runtime is None
            or civitas_state_store is None
            or dynamic_supervisor_name is None
        ):
            raise RuntimeRequiredError(
                "mode='service' requires civitas_runtime, civitas_state_store, "
                "and dynamic_supervisor_name together -- all three are needed "
                "given civitas.runtime.Runtime's real API."
            )
        # Real addition, closing contracts/civitas-bridge.md's open item 1:
        # validate dynamic_supervisor_name upfront via the real, public
        # civitas.runtime.Runtime.get_agent() lookup -- a real, clear error
        # now, not a bus-routing failure surfacing the same misconfiguration
        # less specifically on the first real request_supervision() call.
        if (
            mode == "service"
            and civitas_runtime is not None
            and dynamic_supervisor_name is not None
            and civitas_runtime.get_agent(dynamic_supervisor_name) is None
        ):
            raise SupervisorNotFoundError(
                f"dynamic_supervisor_name={dynamic_supervisor_name!r} does not "
                "resolve to any live agent on the given civitas_runtime -- "
                "check the deployment topology names a DynamicSupervisor with "
                "exactly this name."
            )

        self._mode = mode
        self._summarizer = summarizer
        self._presidium_client = presidium_client
        self._allow_ungoverned = allow_ungoverned
        self._civitas_runtime = civitas_runtime
        self._civitas_state_store = civitas_state_store
        self._dynamic_supervisor_name = dynamic_supervisor_name
        self._overrides = overrides or {}
        self._warm_size = warm_size
        self._max_concurrent = max_concurrent
        self._sandbox_backend = sandbox_backend
        self._tracer: Tracer = tracer if tracer is not None else NullTracer()
        self._built: Fabrica | None = None

    async def build(self) -> Fabrica:
        """Assembles the full object graph -- idempotently. See the module
        docstring and contracts/civitas-bridge.md for what this
        deliberately does NOT do yet (request_supervision for managers --
        structurally incompatible with this codebase's DI-constructed
        managers, see the module docstring).

        contracts/civitas-bridge.md open item 2, resolved: a second call
        returns the SAME `Fabrica` instance, not a second, independent
        object graph. Chosen over "construct a fresh graph each time" or
        "raise on a repeated call" because every other option has a real
        footgun a caller could easily hit by accident -- a second live
        `SandboxPool` (a second warm pool, double the resource footprint,
        neither aware of the other) and, in service mode, a second
        `request_state_persistence()` call per component (unclear whether
        two `ComponentStateHandle`s bound to the same name are safe to use
        concurrently -- untested, not worth risking when "just return what
        was already built" has no such ambiguity). Raising would also be
        a real behavior change for any caller who genuinely calls `build()`
        more than once expecting the same graph back, e.g. from two
        different code paths that both assume they're the first.

        In service mode, MemoryManager/PromptManager get a
        PersistedMemoryStore/PersistedPromptStore backed by a real
        ComponentStateHandle via request_state_persistence -- restarting a
        service-mode CivitasBridge against the same civitas_state_store
        restores prior state, not just a fresh empty store. Library mode
        keeps the original in-memory-only defaults, unchanged.

        Not safe to call concurrently (no internal lock) -- the intended
        usage is one `await build()` at deployment startup, before any
        request-serving concurrency begins, matching every real caller in
        this codebase's own tests. Two truly concurrent first calls could
        each construct an independent graph before either sets the cache;
        deliberately not guarded against, since guarding it would add a
        lock for a scenario this codebase's own usage pattern doesn't hit.
        """
        if self._built is not None:
            return self._built

        presidium_client: PresidiumClient = self._presidium_client or NullPresidiumClient()
        compactor: Compactor = (
            RecencyCompactor(self._summarizer) if self._summarizer is not None else NullCompactor()
        )

        retriever = Retriever(KeywordBackend(), tracer=self._tracer)
        sandbox_pool = SandboxPool(
            self._sandbox_backend or select_sandbox_backend(),
            warm_size=self._warm_size,
            max_concurrent=self._max_concurrent,
            tracer=self._tracer,
        )

        tools = ToolManager(retriever, sandbox_pool, presidium_client, tracer=self._tracer)
        skills = SkillManager(retriever, sandbox_pool, presidium_client, tracer=self._tracer)

        long_term_store: MemoryStoreProtocol
        prompt_store: PromptStoreProtocol
        if self._mode == "service":
            memory_handle = await self.request_state_persistence("memory_manager")
            prompts_handle = await self.request_state_persistence("prompts_manager")
            long_term_store = await PersistedMemoryStore.create(memory_handle)
            prompt_store = await PersistedPromptStore.create(prompts_handle)
        else:
            long_term_store = InMemoryMemoryStore()
            prompt_store = InMemoryPromptStore()

        memory = MemoryManager(
            InMemoryWorkingMemoryStore(), long_term_store, compactor, tracer=self._tracer
        )
        prompts = PromptManager(prompt_store)

        self._built = Fabrica(
            tools=tools, skills=skills, memory=memory, prompts=prompts, sandbox_pool=sandbox_pool
        )
        return self._built

    async def request_supervision(
        self,
        agent_class: type[GenServer],
        name: str,
        config: dict[str, Any] | None = None,
    ) -> str:
        """Only meaningful in service mode. Delegates entirely to
        civitas_runtime.spawn(dynamic_supervisor_name, agent_class, name,
        config) -- CivitasBridge adds no logic of its own beyond supplying
        the supervisor name it was configured with. Not called by build()
        for any of Fabrica's own managers in v1 (see the module docstring)
        -- available for a genuinely fresh, self-contained GenServer-shaped
        component.

        Raises:
            RuntimeRequiredError: called in library mode, where there is
                no civitas_runtime to delegate to.
            Whatever civitas_runtime.spawn itself raises on failure
                (civitas.errors.SpawnError for the real civitas.runtime.Runtime)
                -- propagated unchanged, never caught or re-wrapped here.
        """
        if self._civitas_runtime is None or self._dynamic_supervisor_name is None:
            raise RuntimeRequiredError(
                "request_supervision requires service mode (civitas_runtime and "
                "dynamic_supervisor_name configured at construction)."
            )
        return await self._civitas_runtime.spawn(
            self._dynamic_supervisor_name, agent_class, name, config
        )

    async def request_state_persistence(self, component_name: str) -> ComponentStateHandle:
        """Returns a ComponentStateHandle wrapping civitas_state_store,
        pre-bound to component_name -- the caller never sees the raw
        StateStore or any other component's name. Not called by build()
        for MemoryManager/PromptManager in v1 (see the module docstring)
        -- no StateStore-backed store adapter exists yet to receive the
        handle.

        Raises:
            RuntimeRequiredError: called in library mode, where there is
                no civitas_state_store to bind against.
        """
        if self._civitas_state_store is None:
            raise RuntimeRequiredError(
                "request_state_persistence requires service mode "
                "(civitas_state_store configured at construction)."
            )
        return _BoundStateHandle(self._civitas_state_store, component_name)
