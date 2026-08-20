"""NetworkPolicy -- a default-deny hostname allowlist, and the srt
(Anthropic Sandbox Runtime) settings-JSON translation for it.

This is the concrete answer to isolation.md's "default-deny DNS/network
proxy" requirement: not a hand-rolled DNS server, but srt's own
allow-only network model (`network.allowedDomains`, empty by default =
no network access at all), enforced at the OS level for the whole
process tree (Seatbelt on macOS, bubblewrap+netns on Linux, WFP on
Windows) -- not a convention a sandboxed process could bypass by opening
a raw socket directly, which is a real, confirmed gap in Tier 0's guest
shim (see subprocess_backend.py's module docstring).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class NetworkPolicy:
    """A signed scope document's target allowlist, reduced to exactly what
    srt's settings schema needs. `allowed_domains` matching is srt's own
    (exact-or-subdomain per its `allowedDomains` semantics) -- this class
    does not re-implement or second-guess that matching, only carries the
    list through.
    """

    allowed_domains: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_scope_hosts(cls, hosts: Iterable[str]) -> NetworkPolicy:
        """Build a policy from a scope document's in-scope host list.
        Empty/whitespace-only entries are dropped -- a blank line in a
        scope file must never silently become "no restriction," and
        srt's own schema treats an empty allowedDomains list as "deny
        all," which is the correct default for a malformed/empty scope,
        not an accidental full-open policy.
        """
        return cls(frozenset(h.strip() for h in hosts if h.strip()))

    def is_empty(self) -> bool:
        """True means "deny all network access" under srt's own default-
        deny semantics -- not an error state, but worth a caller checking
        this explicitly before assuming any host is reachable (e.g. to
        surface a clear "this session has no declared scope yet" message
        rather than a silent, confusing network failure later)."""
        return len(self.allowed_domains) == 0

    def to_srt_network_config(
        self,
        *,
        allow_unix_socket_path: str | None = None,
        allow_all_unix_sockets: bool = False,
    ) -> dict[str, object]:
        """The `network` section of an srt settings JSON file. Real,
        confirmed behavior (see specs/archive/spikes -- verified live on
        this machine, not assumed from the README alone): an empty
        `allowedDomains` list denies all network access; a non-empty one
        is allow-only, matching axis 4's "collapses to nothing outside
        scope" requirement exactly.

        `allow_unix_socket_path` is macOS-only and path-based (srt's own
        schema: "Ignored on Linux (seccomp cannot filter by path)") --
        used to let the guest shim's ZMQ ipc:// callback socket through
        without opening Unix sockets generally. `allow_all_unix_sockets`
        is the Linux fallback (no per-path filtering exists there) --
        real, but honestly weaker: it permits creating any Unix socket,
        not just the one the guest shim needs. Both being unset leaves
        Unix sockets fully blocked, which would also break the guest
        shim's own callback bridge -- callers running under Linux+srt
        must set one or the other, matching srt_backend.py's own choice.
        """
        config: dict[str, object] = {
            "allowedDomains": sorted(self.allowed_domains),
            "deniedDomains": [],
            "strictAllowlist": True,
        }
        if allow_unix_socket_path is not None:
            config["allowUnixSockets"] = [allow_unix_socket_path]
        if allow_all_unix_sockets:
            config["allowAllUnixSockets"] = True
        return config
