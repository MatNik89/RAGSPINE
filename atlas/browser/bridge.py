"""In-memory command queue + result rendezvous connecting the API to a
Chrome extension via long-poll. Thread-safe.

Action contract (used by the extension, Task 35):
{"action": "navigate|click|type|scroll|screenshot|read",
 "selector": "...", "value": "...", "url": "..."}
"""
import queue
import threading
import uuid


class Bridge:
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._results: dict[str, dict] = {}
        self.run_timeout = 30
        self.cmd_timeout = 25

    def enqueue(self, cmd: dict) -> str:
        cmd_id = uuid.uuid4().hex
        cmd = {**cmd, "cmd_id": cmd_id}
        with self._lock:
            self._events[cmd_id] = threading.Event()
        self._queue.put(cmd)
        return cmd_id

    def next_cmd(self, timeout: float = 25) -> dict | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def post_result(self, cmd_id: str, result: dict) -> None:
        with self._lock:
            event = self._events.get(cmd_id)
            if event is None:
                return  # unknown or already-timed-out cmd_id — drop it
            self._results[cmd_id] = result
        event.set()

    def wait_result(self, cmd_id: str, timeout: float = 30) -> dict | None:
        with self._lock:
            event = self._events.get(cmd_id)
        if event is None or not event.wait(timeout=timeout):
            with self._lock:
                self._events.pop(cmd_id, None)
                self._results.pop(cmd_id, None)
            return None
        with self._lock:
            result = self._results.pop(cmd_id, None)
            self._events.pop(cmd_id, None)
        return result

    def pending(self) -> int:
        return self._queue.qsize()
