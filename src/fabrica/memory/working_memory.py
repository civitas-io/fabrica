"""WorkingMemoryStore -- see docs/contracts/memory.md."""

from __future__ import annotations

import sys
from typing import Any, Protocol, runtime_checkable

from fabrica.memory.errors import WorkingMemoryQuotaExceeded
from fabrica.scope import Scope

_ScopeKey = tuple[str | None, str | None, str | None, str | None]

DEFAULT_QUOTA_BYTES = 256 * 1024
"""256KB per scope. A guess, not validated against any real working-memory
usage pattern -- contracts/memory.md's open item 2, carried forward
honestly rather than presented as a settled number.
"""


@runtime_checkable
class WorkingMemoryStore(Protocol):
    async def remember(self, scope: Scope, key: str, value: Any) -> None: ...
    async def recall(self, scope: Scope, key: str) -> Any | None: ...
    async def snapshot(self, scope: Scope) -> dict[str, Any]: ...
    async def clear(self, scope: Scope) -> None: ...


class InMemoryWorkingMemoryStore:
    """The default -- zero infra, keyed by the FULL Scope tuple (not just
    session_id) so agents sharing a session_id in some deployment shape
    can't leak working memory into each other.
    """

    def __init__(self, quota_bytes: int = DEFAULT_QUOTA_BYTES) -> None:
        self._quota_bytes = quota_bytes
        self._data: dict[_ScopeKey, dict[str, Any]] = {}

    def _scope_key(self, scope: Scope) -> _ScopeKey:
        return (scope.user_id, scope.session_id, scope.agent_id, scope.team_id)

    async def remember(self, scope: Scope, key: str, value: Any) -> None:
        scope_key = self._scope_key(scope)
        existing = self._data.setdefault(scope_key, {})
        candidate = {**existing, key: value}
        size = sum(sys.getsizeof(k) + sys.getsizeof(v) for k, v in candidate.items())
        if size > self._quota_bytes:
            raise WorkingMemoryQuotaExceeded(
                f"remember({key!r}) would bring scope to {size} bytes, "
                f"exceeding the {self._quota_bytes}-byte quota"
            )
        existing[key] = value

    async def recall(self, scope: Scope, key: str) -> Any | None:
        return self._data.get(self._scope_key(scope), {}).get(key)

    async def snapshot(self, scope: Scope) -> dict[str, Any]:
        return dict(self._data.get(self._scope_key(scope), {}))

    async def clear(self, scope: Scope) -> None:
        self._data.pop(self._scope_key(scope), None)
