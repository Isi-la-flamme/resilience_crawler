import json
import threading
import time
from contextlib import contextmanager


class Metrics:
    def __init__(self):
        self._durations = {}
        self._counts = {}
        self._lock = threading.Lock()

    @contextmanager
    def measure(self, phase):
        started = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - started
            with self._lock:
                self._durations[phase] = self._durations.get(phase, 0.0) + elapsed
                self._counts[phase] = self._counts.get(phase, 0) + 1

    def summary(self):
        with self._lock:
            return {
                phase: {
                    "total_seconds": round(self._durations[phase], 4),
                    "calls": self._counts[phase],
                    "avg_ms": round(
                        1000 * self._durations[phase] / self._counts[phase], 2
                    ),
                }
                for phase in self._durations
            }

    def dump(self, path):
        path.write_text(
            json.dumps(self.summary(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def reset(self):
        with self._lock:
            self._durations.clear()
            self._counts.clear()


metrics = Metrics()