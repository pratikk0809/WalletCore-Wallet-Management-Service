"""In-memory sliding-window rate limiter. Pure Python: no FastAPI, no database."""

import math
import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sweep_every: int = 1000,
    ) -> None:
        self._clock = clock
        self._sweep_every = sweep_every
        self._hits: dict[str, deque[float]] = {}  # key -> timestamps of allowed requests
        self._lock = threading.Lock()  # endpoints run in worker threads
        self._calls = 0

    def hit(self, key: str, limit: int, window_seconds: int) -> int | None:
        """Register one request for `key`.

        Returns None if it is allowed, or the number of seconds to wait
        (at least 1) if the limit is exceeded. Blocked requests are NOT recorded.
        """
        now = self._clock()
        with self._lock:
            self._calls += 1
            if self._calls % self._sweep_every == 0:
                self._sweep(now, window_seconds)

            hits = self._hits.setdefault(key, deque())
            cutoff = now - window_seconds
            while hits and hits[0] <= cutoff:  # forget requests outside the window
                hits.popleft()

            if len(hits) >= limit:
                # The oldest request in the window leaves it at hits[0] + window.
                return max(1, math.ceil(hits[0] + window_seconds - now))

            hits.append(now)
            return None

    def reset(self) -> None:
        """Forget everything (used by tests)."""
        with self._lock:
            self._hits.clear()
            self._calls = 0

    def _sweep(self, now: float, window_seconds: int) -> None:
        """Drop keys whose requests have all expired, so memory can't grow forever."""
        cutoff = now - window_seconds
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= cutoff]:
            del self._hits[key]


# One shared limiter for the whole app.
rate_limiter = RateLimiter()
