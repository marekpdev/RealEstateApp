import threading
import time
from enum import Enum
from typing import Dict, Optional

from config import config


class CircuitState(str, Enum):
    """
    CLOSED: calls flow through normally; failures are just being counted.
    OPEN: every call is refused immediately, with no network call at all.
    HALF_OPEN: the reset timeout has elapsed - exactly one probe call is
    let through to test whether the upstream has recovered, while every
    other concurrent call is still refused.
    """
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """
    Raised by CircuitBreaker.before_call() when a call must be refused
    outright. The whole point of a circuit breaker is that this check never
    touches the network - unlike a retry loop, which still hits the wire on
    every attempt, this fails in microseconds while the breaker is open.
    """

    def __init__(self, name: str, retry_after_seconds: float):
        self.name = name
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Circuit breaker '{name}' is open - refusing the call without "
            f"contacting the upstream. Retry in {retry_after_seconds:.1f}s."
        )


class CircuitBreaker:
    """
    A per-upstream circuit breaker with three states (see CircuitState).

    CLOSED -> OPEN once `failure_threshold` consecutive failures have been
    recorded. OPEN -> HALF_OPEN once `reset_timeout_seconds` has elapsed
    since it tripped. HALF_OPEN -> CLOSED on a successful probe, or back to
    OPEN (with the reset timer restarted) on a failed one.

    Deliberately in-memory and process-local rather than Redis-backed like
    rate_limit/token_bucket.py's limiter: a breaker's job is to stop *this*
    process from continuing to hammer an upstream that has just demonstrated
    it can't handle traffic, which needs no agreement across replicas the
    way the rate limiter's shared per-identity quota does.
    """

    def __init__(self, name: str, failure_threshold: int, reset_timeout_seconds: float):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: Optional[float] = None
        self._half_open_probe_claimed = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> CircuitState:
        """Lazily promotes OPEN -> HALF_OPEN once the reset timeout has
        elapsed, rather than relying on a background timer - the state is
        computed the moment something actually asks for it."""
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.reset_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_claimed = False
        return self._state

    def before_call(self) -> None:
        """Raises CircuitBreakerOpenError if this call must not proceed.
        Callers are expected to call this immediately before the real
        network call, then report the outcome via record_success()/
        record_failure()."""
        with self._lock:
            state = self._state_locked()
            if state == CircuitState.CLOSED:
                return
            if state == CircuitState.OPEN:
                remaining = self.reset_timeout_seconds - (time.monotonic() - self._opened_at)
                raise CircuitBreakerOpenError(self.name, max(remaining, 0.0))
            # HALF_OPEN: let exactly one probe through; any other caller
            # arriving while that probe is still in flight is refused too,
            # exactly like OPEN, rather than letting a burst of concurrent
            # callers all re-hit a not-yet-confirmed-healthy upstream at once.
            if self._half_open_probe_claimed:
                raise CircuitBreakerOpenError(self.name, self.reset_timeout_seconds)
            self._half_open_probe_claimed = True

    def record_success(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._opened_at = None
            self._half_open_probe_claimed = False

    def record_failure(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # The probe call itself failed - the upstream isn't
                # actually recovered yet. Reopen and restart the timer
                # rather than letting the failure count creep back up from
                # zero, which would take failure_threshold more failures
                # to reopen instead of the one probe that just proved
                # nothing has changed.
                self._trip_locked()
                return
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._trip_locked()

    def _trip_locked(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._failure_count = 0
        self._half_open_probe_claimed = False


_breakers: Dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """Lazy per-name singleton registry - one breaker per upstream (keyed
    by e.g. a vendor's base_url), never one shared/global breaker, so a
    struggling vendor can't trip a breaker that also blocks calls to an
    unrelated, healthy one."""
    with _breakers_lock:
        breaker = _breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name=name,
                failure_threshold=config.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
                reset_timeout_seconds=config.CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS,
            )
            _breakers[name] = breaker
        return breaker


def reset_circuit_breakers() -> None:
    """Test-only: clears the registry so every named breaker starts fresh -
    the in-memory-state equivalent of tests/conftest.py's per-test Redis
    flush for the rate limiter's own bucket state."""
    with _breakers_lock:
        _breakers.clear()
