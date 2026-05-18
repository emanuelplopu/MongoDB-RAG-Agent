"""MongoDB circuit breaker for scheduler write resilience."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing — buffer writes
    HALF_OPEN = "half_open"  # Testing recovery


class MongoDBCircuitBreaker:
    """Circuit breaker pattern for MongoDB write operations.

    States:
    - CLOSED: Normal operation, writes go directly to DB.
    - OPEN: DB failing, buffer writes in memory (max 100 items).
    - HALF_OPEN: Testing if DB recovered, next write is a probe.

    Transitions:
    - CLOSED → OPEN: after failure_threshold (3) consecutive failures.
    - OPEN → HALF_OPEN: after recovery_timeout (30s).
    - HALF_OPEN → CLOSED: on successful probe write.
    - HALF_OPEN → OPEN: on failed probe write.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        max_buffer_size: int = 100,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout_seconds
        self.max_buffer_size = max_buffer_size

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._buffer: deque = deque(maxlen=max_buffer_size)

    @property
    def state(self) -> CircuitState:
        """Current circuit state (may auto-transition OPEN → HALF_OPEN)."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                logger.info(
                    "Circuit breaker: OPEN → HALF_OPEN after %.1fs recovery timeout",
                    elapsed,
                )
                self._state = CircuitState.HALF_OPEN
        return self._state

    async def execute(
        self, operation: Callable, *args: Any, **kwargs: Any
    ) -> Optional[Any]:
        """Execute a DB write operation through the circuit breaker.

        - CLOSED: execute directly, track failures.
        - OPEN: buffer the operation, don't attempt.
        - HALF_OPEN: attempt as probe, transition based on result.
        """
        current_state = self.state  # triggers auto-transition check

        if current_state == CircuitState.OPEN:
            # Buffer the operation for later replay
            self._buffer.append((operation, args, kwargs))
            logger.debug(
                "Circuit OPEN — buffered operation (buffer size: %d)",
                len(self._buffer),
            )
            return None

        # CLOSED or HALF_OPEN: attempt the operation
        try:
            if asyncio.iscoroutinefunction(operation):
                result = await operation(*args, **kwargs)
            else:
                result = operation(*args, **kwargs)
            self.record_success()
            return result
        except Exception as exc:
            self.record_failure()
            if current_state == CircuitState.HALF_OPEN:
                logger.warning(
                    "Circuit breaker probe FAILED — back to OPEN: %s", exc
                )
            else:
                logger.warning(
                    "Circuit breaker: write failure #%d: %s",
                    self._failure_count,
                    exc,
                )
            # Buffer the failed operation for retry
            self._buffer.append((operation, args, kwargs))
            return None

    async def flush_buffer(self, db: Any = None) -> int:
        """Flush buffered operations to DB after recovery.

        Returns number of successfully flushed items.
        """
        if not self._buffer:
            return 0

        flushed = 0
        remaining: deque = deque(maxlen=self.max_buffer_size)

        while self._buffer:
            operation, args, kwargs = self._buffer.popleft()
            try:
                if asyncio.iscoroutinefunction(operation):
                    await operation(*args, **kwargs)
                else:
                    operation(*args, **kwargs)
                flushed += 1
            except Exception as exc:
                logger.warning("flush_buffer: operation failed: %s", exc)
                remaining.append((operation, args, kwargs))
                # Put remaining items back
                remaining.extend(self._buffer)
                self._buffer = remaining
                break

        logger.info(
            "flush_buffer: flushed %d operations, %d remaining",
            flushed,
            len(self._buffer),
        )
        return flushed

    def record_success(self) -> None:
        """Record a successful operation (resets failure count)."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info("Circuit breaker: HALF_OPEN → CLOSED (probe succeeded)")
            self._state = CircuitState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed operation (may trigger OPEN state)."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._failure_count >= self.failure_threshold:
            if self._state != CircuitState.OPEN:
                logger.warning(
                    "Circuit breaker: → OPEN after %d consecutive failures",
                    self._failure_count,
                )
            self._state = CircuitState.OPEN

    def reset(self) -> None:
        """Reset circuit breaker to initial state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._buffer.clear()
        logger.info("Circuit breaker reset to CLOSED")

    @property
    def buffer_size(self) -> int:
        """Number of buffered operations waiting for flush."""
        return len(self._buffer)
