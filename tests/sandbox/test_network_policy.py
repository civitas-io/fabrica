"""Tests for NetworkPolicy -- pure logic, no subprocess/network I/O.
The real proof this policy actually blocks/allows real network access
under srt lives in test_srt_backend.py.
"""

from __future__ import annotations

from fabrica.sandbox.network_policy import NetworkPolicy


def test_empty_policy_is_empty() -> None:
    policy = NetworkPolicy()
    assert policy.is_empty() is True


def test_from_scope_hosts_builds_frozenset() -> None:
    policy = NetworkPolicy.from_scope_hosts(["example.com", "api.example.com"])
    assert policy.allowed_domains == frozenset({"example.com", "api.example.com"})
    assert policy.is_empty() is False


def test_from_scope_hosts_drops_blank_entries() -> None:
    # A blank line in a scope file must never silently become "no
    # restriction" -- it's dropped, not treated as a wildcard or error.
    policy = NetworkPolicy.from_scope_hosts(["example.com", "", "   ", "api.example.com"])
    assert policy.allowed_domains == frozenset({"example.com", "api.example.com"})


def test_from_scope_hosts_strips_whitespace() -> None:
    policy = NetworkPolicy.from_scope_hosts([" example.com \n"])
    assert policy.allowed_domains == frozenset({"example.com"})


def test_from_scope_hosts_with_no_hosts_is_empty() -> None:
    policy = NetworkPolicy.from_scope_hosts([])
    assert policy.is_empty() is True


def test_srt_network_config_empty_policy_denies_all() -> None:
    # srt's own semantics: an empty allowedDomains list denies all
    # network access -- the correct default for a malformed/empty scope,
    # not an accidental full-open policy. Verified live against real srt
    # in test_srt_backend.py; this test only checks the config shape.
    config = NetworkPolicy().to_srt_network_config()
    assert config["allowedDomains"] == []
    assert config["strictAllowlist"] is True


def test_srt_network_config_sorts_domains_deterministically() -> None:
    policy = NetworkPolicy.from_scope_hosts(["zeta.example.com", "alpha.example.com"])
    config = policy.to_srt_network_config()
    assert config["allowedDomains"] == ["alpha.example.com", "zeta.example.com"]


def test_srt_network_config_strict_allowlist_always_true() -> None:
    # "Set this when allowedDomains is policy enforcement, not a
    # prompt-suppression hint" -- per srt's own schema docstring. Kordon's
    # scope document IS policy enforcement, never a suggestion an
    # interactive "ask" callback could override.
    config = NetworkPolicy.from_scope_hosts(["example.com"]).to_srt_network_config()
    assert config["strictAllowlist"] is True


def test_srt_network_config_no_unix_socket_keys_by_default() -> None:
    config = NetworkPolicy().to_srt_network_config()
    assert "allowUnixSockets" not in config
    assert "allowAllUnixSockets" not in config


def test_srt_network_config_sets_macos_path_based_unix_socket_allow() -> None:
    config = NetworkPolicy().to_srt_network_config(allow_unix_socket_path="/tmp/foo.sock")
    assert config["allowUnixSockets"] == ["/tmp/foo.sock"]
    assert "allowAllUnixSockets" not in config


def test_srt_network_config_sets_linux_allow_all_unix_sockets_flag() -> None:
    config = NetworkPolicy().to_srt_network_config(allow_all_unix_sockets=True)
    assert config["allowAllUnixSockets"] is True
    assert "allowUnixSockets" not in config


def test_srt_network_config_denied_domains_always_empty() -> None:
    # Kordon's model is allow-only (axis 4: "collapses to nothing outside
    # scope"), never a denylist -- deniedDomains is deliberately always
    # empty, not populated from anywhere.
    config = NetworkPolicy.from_scope_hosts(["example.com"]).to_srt_network_config()
    assert config["deniedDomains"] == []
