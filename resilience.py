"""
Victor-OS Resilience Framework
Retry with exponential backoff + Circuit Breaker for external APIs.
"""

import time
import threading
import functools
from typing import Callable, Any, TypeVar

from logging_config import get_logger
from errors import APIError

logger = get_logger("resilience")
T = TypeVar("T")


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable:
    """Decorator: retries a function with exponential backoff."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(
                            f"{func.__name__} failed after {max_retries + 1} attempts: {e}",
                            extra={"tool_name": func.__name__},
                        )
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        f"{func.__name__} attempt {attempt + 1} failed: {e}. Retrying in {delay:.1f}s...",
                        extra={"tool_name": func.__name__},
                    )
                    time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator


class CircuitBreaker:
    """Circuit breaker for external service calls.

    States: CLOSED (normal) -> OPEN (failing) -> HALF_OPEN (testing recovery)
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == self.OPEN:
                if time.time() - self._last_failure_time > self.recovery_timeout:
                    self._state = self.HALF_OPEN
            return self._state

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute func through the circuit breaker."""
        current_state = self.state
        if current_state == self.OPEN:
            logger.warning(f"Circuit '{self.name}' is OPEN — call rejected")
            raise APIError(f"Circuit breaker '{self.name}' is open. Service temporarily unavailable.", recoverable=True)

        try:
            result = func(*args, **kwargs)
            with self._lock:
                self._failure_count = 0
                self._state = self.CLOSED
            return result
        except Exception as e:
            with self._lock:
                self._failure_count += 1
                self._last_failure_time = time.time()
                if self._failure_count >= self.failure_threshold:
                    self._state = self.OPEN
                    logger.error(
                        f"Circuit '{self.name}' tripped to OPEN after {self._failure_count} failures"
                    )
            raise

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED."""
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0


# Pre-built circuit breakers for external services
gemini_breaker = CircuitBreaker("gemini", failure_threshold=3, recovery_timeout=60.0)
telegram_breaker = CircuitBreaker("telegram", failure_threshold=5, recovery_timeout=30.0)
twilio_breaker = CircuitBreaker("twilio", failure_threshold=5, recovery_timeout=30.0)
