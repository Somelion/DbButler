"""Backoff for targets the scheduler can't connect to.

Without it, an unreachable target costs every job its full connect timeout,
every cycle, and keeps logging the same error. After a failed connect the
target is skipped for a while, doubling each time it fails again, until one
connect succeeds.
"""

import threading
import time
from collections.abc import Callable

BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 600


class TargetBackoff:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        # target_id -> (consecutive failures, don't retry before this clock value)
        self._state: dict[object, tuple[int, float]] = {}

    def should_attempt(self, target_id) -> bool:
        with self._lock:
            state = self._state.get(target_id)
        return state is None or self._clock() >= state[1]

    def record_failure(self, target_id) -> float:
        """Returns how many seconds the target will now be skipped for."""
        with self._lock:
            failures = self._state.get(target_id, (0, 0.0))[0] + 1
            delay = min(BACKOFF_BASE_SECONDS * 2 ** (failures - 1), BACKOFF_MAX_SECONDS)
            self._state[target_id] = (failures, self._clock() + delay)
        return delay

    def record_success(self, target_id) -> None:
        with self._lock:
            self._state.pop(target_id, None)
