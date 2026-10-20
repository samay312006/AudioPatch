"""Streaming linear-interpolation resampler with an adjustable ratio.

Used for sources whose native rate isn't the engine rate, and (phase 4) for
tiny ratio nudges that cancel clock drift between devices. State carries
across blocks so there are no clicks at block boundaries.
"""
from __future__ import annotations

import numpy as np


class StreamResampler:
    def __init__(self, ratio: float = 1.0, channels: int = 2):
        self.ratio = ratio            # output rate / input rate
        self.channels = channels
        self._prev = np.zeros((1, channels), dtype=np.float32)  # last input frame
        self._pos = 0.0               # fractional read position, relative to _prev

    def process(self, block: np.ndarray) -> np.ndarray:
        if len(block) == 0:
            return np.zeros((0, self.channels), dtype=np.float32)
        if self.ratio == 1.0 and self._pos == 0.0:
            self._prev = block[-1:].copy()
            return block
        src = np.concatenate([self._prev, block])           # src[0] is the previous frame
        step = 1.0 / self.ratio
        # Output positions t in [pos, len(block)), measured in src coordinates.
        count = int(np.ceil((len(block) - self._pos) / step))
        t = self._pos + step * np.arange(max(count, 0))
        t = t[t < len(block)]
        i = t.astype(np.int64)
        frac = (t - i).astype(np.float32)[:, None]
        out = src[i] * (1.0 - frac) + src[i + 1] * frac
        self._pos = (t[-1] + step - len(block)) if len(t) else self._pos - len(block)
        self._prev = block[-1:].copy()
        return out.astype(np.float32, copy=False)
