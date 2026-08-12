"""Sliding-window rate-limit (TIER 2). In-memory per app instance — enough for a
single-process deploy; ponytail: the multi-worker/multi-node upgrade path is the
same API over a Redis/SQLite backend."""
import threading
import time
from collections import deque


class RateLimiter:
    _MAX_KEYS = 10000

    def __init__(self):
        self._lock = threading.Lock()
        self._events: dict[str, deque] = {}
        self._max_window = 60.0

    def allow(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            self._max_window = max(self._max_window, window_s)
            dq = self._events.setdefault(key, deque())
            while dq and now - dq[0] > window_s:
                dq.popleft()
            if len(dq) >= limit:
                return False
            dq.append(now)
            if len(self._events) > self._MAX_KEYS:
                self._evict(now)
            return True

    def _evict(self, now: float) -> None:
        """Codex findings (2 rounds): (1) cold keys never drain on their own —
        sweep ALL keys by the largest seen window; (2) the sweep alone is not a CAP —
        with only fresh (attacker) keys it deletes nothing, so above the cap the
        oldest-active keys are additionally evicted down to 90% of the cap. Legitimate
        active users are recently active and thus survive; the 10% hysteresis
        amortizes the O(n log n) over rare calls."""
        dead = []
        for k, dq in self._events.items():
            while dq and now - dq[0] > self._max_window:
                dq.popleft()
            if not dq:
                dead.append(k)
        for k in dead:
            del self._events[k]
        if len(self._events) > self._MAX_KEYS:
            by_last_activity = sorted(self._events, key=lambda k: self._events[k][-1])
            for k in by_last_activity[: len(self._events) - int(self._MAX_KEYS * 0.9)]:
                del self._events[k]
