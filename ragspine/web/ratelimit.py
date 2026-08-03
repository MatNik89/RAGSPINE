"""Sliding-window rate-limit (TIER 2). In-memory po app instanci — dovoljno za
single-process deploy; ponytail: multi-worker/multi-node upgrade path je isti
API nad Redis/SQLite backendom."""
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
        """Codex nalaz: hladni ključevi se sami nikad ne isprazne (prune ide samo
        kroz allow() za TAJ ključ) — napadač s jedinstvenim usernameovima raste
        memoriju bez granice. Zato sweep SVIH ključeva po najvećem viđenom
        prozoru + brisanje ispražnjenih; poziva se tek iznad _MAX_KEYS."""
        dead = []
        for k, dq in self._events.items():
            while dq and now - dq[0] > self._max_window:
                dq.popleft()
            if not dq:
                dead.append(k)
        for k in dead:
            del self._events[k]
