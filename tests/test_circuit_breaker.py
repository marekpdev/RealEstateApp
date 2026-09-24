from unittest.mock import patch

import pytest

from resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    get_circuit_breaker,
)


def _breaker(failure_threshold=3, reset_timeout_seconds=10.0) -> CircuitBreaker:
    return CircuitBreaker(
        name="test-upstream",
        failure_threshold=failure_threshold,
        reset_timeout_seconds=reset_timeout_seconds,
    )


def test_starts_closed_and_allows_calls():
    breaker = _breaker()
    assert breaker.state == CircuitState.CLOSED
    breaker.before_call()  # must not raise


def test_a_success_below_threshold_never_trips_it():
    breaker = _breaker(failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
    breaker.before_call()


def test_a_success_resets_the_failure_count():
    """Two failures, then a success, then two more failures must not trip a
    threshold-of-3 breaker - the count only tracks *consecutive* failures."""
    breaker = _breaker(failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED


def test_threshold_consecutive_failures_trip_it_open():
    breaker = _breaker(failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


def test_open_breaker_refuses_calls_without_hitting_anything():
    breaker = _breaker(failure_threshold=1, reset_timeout_seconds=999.0)
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    with pytest.raises(CircuitBreakerOpenError) as excinfo:
        breaker.before_call()
    assert excinfo.value.name == "test-upstream"
    assert excinfo.value.retry_after_seconds > 0


def test_open_breaker_transitions_to_half_open_after_reset_timeout():
    with patch("resilience.circuit_breaker.time.monotonic") as mock_clock:
        mock_clock.return_value = 1000.0
        breaker = _breaker(failure_threshold=1, reset_timeout_seconds=30.0)
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        mock_clock.return_value = 1000.0 + 29.0
        assert breaker.state == CircuitState.OPEN

        mock_clock.return_value = 1000.0 + 30.0
        assert breaker.state == CircuitState.HALF_OPEN


def test_half_open_lets_exactly_one_probe_through():
    with patch("resilience.circuit_breaker.time.monotonic") as mock_clock:
        mock_clock.return_value = 0.0
        breaker = _breaker(failure_threshold=1, reset_timeout_seconds=10.0)
        breaker.record_failure()

        mock_clock.return_value = 10.0
        assert breaker.state == CircuitState.HALF_OPEN

        breaker.before_call()  # the one probe - must not raise

        with pytest.raises(CircuitBreakerOpenError):
            breaker.before_call()  # a second, concurrent caller is refused


def test_a_successful_probe_closes_the_breaker():
    with patch("resilience.circuit_breaker.time.monotonic") as mock_clock:
        mock_clock.return_value = 0.0
        breaker = _breaker(failure_threshold=1, reset_timeout_seconds=10.0)
        breaker.record_failure()

        mock_clock.return_value = 10.0
        breaker.before_call()
        breaker.record_success()

        assert breaker.state == CircuitState.CLOSED
        breaker.before_call()  # calls flow normally again


def test_a_failed_probe_reopens_and_restarts_the_timer():
    with patch("resilience.circuit_breaker.time.monotonic") as mock_clock:
        mock_clock.return_value = 0.0
        breaker = _breaker(failure_threshold=1, reset_timeout_seconds=10.0)
        breaker.record_failure()

        mock_clock.return_value = 10.0
        breaker.before_call()
        breaker.record_failure()  # the probe itself failed
        assert breaker.state == CircuitState.OPEN

        # still within the freshly-restarted window - stays open
        mock_clock.return_value = 15.0
        assert breaker.state == CircuitState.OPEN

        # a full reset_timeout after the *second* trip - half-open again
        mock_clock.return_value = 20.0
        assert breaker.state == CircuitState.HALF_OPEN


def test_get_circuit_breaker_is_a_per_name_singleton():
    breaker_a = get_circuit_breaker("vendor-a")
    breaker_b = get_circuit_breaker("vendor-a")
    breaker_c = get_circuit_breaker("vendor-b")

    assert breaker_a is breaker_b
    assert breaker_a is not breaker_c


def test_get_circuit_breaker_isolates_failures_per_upstream():
    """Tripping one named breaker must never affect a different one - the
    whole point of per-upstream breakers over a single global one."""
    with patch.multiple(
        "resilience.circuit_breaker.config",
        CIRCUIT_BREAKER_FAILURE_THRESHOLD=1,
        CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS=999.0,
    ):
        vendor_a = get_circuit_breaker("vendor-a")
        vendor_b = get_circuit_breaker("vendor-b")

        vendor_a.record_failure()

        assert vendor_a.state == CircuitState.OPEN
        assert vendor_b.state == CircuitState.CLOSED
        vendor_b.before_call()  # must not raise
