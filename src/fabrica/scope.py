"""Scope -- reused by MemoryStore, PresidiumClient, and the usage-ledger span
attributes (memory.md, system-design.md §7). One type, not redefined per
surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scope:
    user_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    team_id: str | None = None  # shared with usage/budget rollups

    extra: dict[str, str] = field(default_factory=dict)
    """Additive, optional catch-all for arbitrary action-specific context a CEL
    policy might need beyond agent identity (e.g. "which target host is this
    action aimed at"). civitas-io/presidium's real, current check_grant() server
    side deserializes the whole ``scope`` JSON object into
    ``ActionRequest.parameters`` (a real, previously-unfixed gap closed
    2026-08-24) -- so any key here becomes real, live policy-referenceable data
    (``request.parameters.<key>``) with zero server-side changes needed.

    Merged **flat** into the wire payload by
    ``RestPresidiumClient._scope_to_dict()`` -- NOT nested under an ``"extra"``
    key -- so ``extra={"target_host": "db1"}`` shows up to a policy as
    ``request.parameters.target_host``, not
    ``request.parameters.extra.target_host``.

    Empty by default: existing callers are unaffected. Raises ``ValueError`` at
    construction time (``__post_init__``, below) if a key here collides with
    one of the four fixed field names above -- reserved, never silently
    overridden. Validated at construction, not serialization: every
    ``PresidiumClient`` implementation's own documented contract is "never
    raises" (matching the ``Protocol``, e.g. ``RestPresidiumClient.
    check_grant()``'s network-failure-as-a-return-value design) -- a caller
    mistake like this must surface immediately and loudly at the point the
    bad ``Scope`` is built, not get silently absorbed into a generic
    ``deny`` result deep inside a client that was never designed to
    distinguish "caller error" from "server unreachable."
    """

    _RESERVED_FIELD_NAMES = frozenset({"user_id", "session_id", "agent_id", "team_id"})

    def __post_init__(self) -> None:
        collisions = self._RESERVED_FIELD_NAMES & self.extra.keys()
        if collisions:
            raise ValueError(
                f"Scope.extra key(s) {sorted(collisions)} collide with reserved Scope field name(s)"
            )
