"""Sliding-window rate-limit (TIER 2). In-memory po app instanci — dovoljno za
single-process deploy; ponytail: multi-worker/multi-node upgrade path je isti
API nad Redis/SQLite backendom."""
import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._events: dict[str, deque] = {}

    def allow(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            dq = self._events.setdefault(key, deque())
            while dq and now - dq[0] > window_s:
                dq.popleft()
            if len(dq) >= limit:
                return False
            dq.append(now)
            if len(self._events) > 10000:  # zaboravi hladne ključeve (memorija)
                for k in [k for k, d in self._events.items() if not d]:
                    del self._events[k]
            return True
