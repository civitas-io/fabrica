"""Tests for RestPresidiumClient's internal _CircuitBreaker --
system-design.md §6's own spec: trip open after N consecutive failures,
short-circuit immediately (no fresh timeout wait) until a cooldown
elapses, then half-open to test recovery.
"""

from __future__ import annotations

from fabrica.presidium.rest_client import _CircuitBreaker


class _FakeClock:
    """Deterministic, injectable clock -- no real sleeps in these tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _breaker(
    *, failure_threshold: int = 3, cooldown_seconds: float = 10.0
) -> tuple[_CircuitBreaker, _FakeClock]:
    clock = _FakeClock()
    return (
        _CircuitBreaker(
            failure_threshold=failure_threshold, cooldown_seconds=cooldown_seconds, _now=clock
        ),
        clock,
    )


class TestClosedState:
    def test_starts_closed_allowing_requests(self) -> None:
        breaker, _ = _breaker()
        assert breaker.state == "closed"
        assert breaker.allow_request() is True

    def test_success_keeps_it_closed(self) -> None:
        breaker, _ = _breaker()
        breaker.record_success()
        assert breaker.state == "closed"
        assert breaker.allow_request() is True

    def test_failures_below_threshold_stay_closed(self) -> None:
        breaker, _ = _breaker(failure_threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == "closed"
        assert breaker.allow_request() is True

    def test_a_success_between_failures_resets_the_consecutive_count(self) -> None:
        """The real point of "consecutive" -- two isolated failures with a
        success between them must not trip the breaker, only a genuine
        unbroken run of failures should."""
        breaker, _ = _breaker(failure_threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == "closed"
        assert breaker.allow_request() is True


class TestOpensAtThreshold:
    def test_reaching_the_threshold_opens_the_breaker(self) -> None:
        breaker, _ = _breaker(failure_threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == "open"

    def test_open_breaker_denies_without_waiting_for_a_fresh_timeout(self) -> None:
        """The real point of a circuit breaker over a plain retry loop:
        once open, subsequent calls are refused immediately -- never
        attempted against the network at all."""
        breaker, clock = _breaker(failure_threshold=1, cooldown_seconds=100.0)
        breaker.record_failure()
        assert breaker.state == "open"
        clock.advance(1.0)  # nowhere near the 100s cooldown
        assert breaker.allow_request() is False


class TestCooldownAndHalfOpen:
    def test_before_cooldown_elapses_stays_open(self) -> None:
        breaker, clock = _breaker(failure_threshold=1, cooldown_seconds=10.0)
        breaker.record_failure()
        clock.advance(9.9)
        assert breaker.allow_request() is False

    def test_cooldown_elapsed_transitions_to_half_open_and_allows_one_request(self) -> None:
        breaker, clock = _breaker(failure_threshold=1, cooldown_seconds=10.0)
        breaker.record_failure()
        clock.advance(10.0)
        assert breaker.allow_request() is True
        assert breaker.state == "half_open"

    def test_half_open_success_closes_the_breaker(self) -> None:
        breaker, clock = _breaker(failure_threshold=1, cooldown_seconds=10.0)
        breaker.record_failure()
        clock.advance(10.0)
        assert breaker.allow_request() is True
        breaker.record_success()
        assert breaker.state == "closed"
        assert breaker.allow_request() is True

    def test_half_open_failure_reopens_with_a_fresh_cooldown(self) -> None:
        """A single failed recovery attempt must not immediately allow
        another -- it goes straight back to a full cooldown, not a
        shorter or skipped one."""
        breaker, clock = _breaker(failure_threshold=1, cooldown_seconds=10.0)
        breaker.record_failure()
        clock.advance(10.0)
        assert breaker.allow_request() is True  # half-open, one real attempt
        breaker.record_failure()
        assert breaker.state == "open"
        clock.advance(9.9)
        assert breaker.allow_request() is False
        clock.advance(0.1)
        assert breaker.allow_request() is True

    def test_default_clock_is_real_monotonic_time(self) -> None:
        """Confirms the default (_now=None) path uses a real clock, not
        just that the injectable one works -- both code paths matter."""
        breaker = _CircuitBreaker(failure_threshold=1, cooldown_seconds=0.0)
        breaker.record_failure()
        assert breaker.state == "open"
        # cooldown_seconds=0.0 means any real elapsed time (even ~0) clears it
        assert breaker.allow_request() is True
