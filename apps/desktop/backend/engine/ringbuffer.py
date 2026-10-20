"""Single-producer / single-consumer ring buffer of float32 frames.

The producer only advances the write counter and the consumer only advances
the read counter, so no lock is needed (int assignment is atomic under the
GIL, and data is copied before the counter moves). On overflow the producer
drops the newest frames; the consumer bounds latency itself with skip().
"""
from __future__ import annotations

import numpy as np


class RingBuffer:
    def __init__(self, capacity: int, channels: int = 2):
        self.capacity = int(capacity)
        self.channels = channels
        self._buf = np.zeros((self.capacity, channels), dtype=np.float32)
        self._w = 0  # total frames ever written (producer-owned)
        self._r = 0  # total frames ever read (consumer-owned)
        self.overruns = 0

    @property
    def available(self) -> int:
        return self._w - self._r

    def write(self, block: np.ndarray) -> int:
        n = min(len(block), self.capacity - self.available)
        if n < len(block):
            self.overruns += 1
        if n <= 0:
            return 0
        start = self._w % self.capacity
        first = min(n, self.capacity - start)
        self._buf[start:start + first] = block[:first]
        if n > first:
            self._buf[:n - first] = block[first:n]
        self._w += n
        return n

    def read_into(self, out: np.ndarray) -> int:
        """Fill out[:k] with up to len(out) frames; returns k."""
        n = min(len(out), self.available)
        if n <= 0:
            return 0
        start = self._r % self.capacity
        first = min(n, self.capacity - start)
        out[:first] = self._buf[start:start + first]
        if n > first:
            out[first:n] = self._buf[:n - first]
        self._r += n
        return n

    def skip(self, n: int) -> None:
        self._r += max(0, min(n, self.available))

    def clear(self) -> None:
        self._r = self._w
