"""One workspace-wide ceiling on concurrent agent sessions (#728).

Report, review and main-lane dispatch each had their own limit, so the real
peak was reports x (1 + Scouts) + reviews + 1. Dispatch now reserves sessions
here first. A report reserves itself plus one Scout; before its research starts
it may grow up to the Scout count it wants, limited by what is still free.

A review that belongs to a running report uses that report's reservation: the
report waits for it, so charging it again could leave both waiting forever.
"""
import threading


class ModelBudget:
    def __init__(self, limit):
        self._limit = limit
        self._lock = threading.Lock()
        self._held = {}
        self.peak = 0

    @property
    def limit(self):
        return max(1, int(self._limit()))

    def free(self):
        with self._lock:
            return self.limit - sum(self._held.values())

    def reserve(self, key, count):
        """Hold `count` sessions for `key` if they are all free; False otherwise."""
        with self._lock:
            if self.limit - sum(self._held.values()) < count:
                return False
            self._held[key] = self._held.get(key, 0) + count
            self.peak = max(self.peak, sum(self._held.values()))
            return True

    def grow(self, key, want):
        """Raise `key` towards `want` total sessions; returns what it now holds."""
        with self._lock:
            held = self._held.get(key, 0)
            extra = max(0, min(want - held, self.limit - sum(self._held.values())))
            if extra:
                self._held[key] = held + extra
                self.peak = max(self.peak, sum(self._held.values()))
            return held + extra

    def release(self, key):
        with self._lock:
            self._held.pop(key, None)

    def snapshot(self):
        with self._lock:
            used = sum(self._held.values())
            return {'limit': self.limit, 'in_use': used, 'free': self.limit - used,
                    'peak': self.peak, 'held': dict(self._held)}
