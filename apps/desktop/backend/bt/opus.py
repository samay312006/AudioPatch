"""Minimal libopus encoder via ctypes (the DLL ships in the small `pyogg` wheel).
Only what the Bluetooth link needs: 48 kHz stereo, 20 ms frames, fixed bitrate."""
from __future__ import annotations

import ctypes
import os
import sys

import numpy as np

OPUS_APPLICATION_AUDIO = 2049
OPUS_SET_BITRATE = 4002
OPUS_SET_VBR_CONSTRAINT = 4020
OPUS_SET_COMPLEXITY = 4010


def _load() -> ctypes.CDLL:
    names = ["opus.dll"] if sys.platform == "win32" else ["libopus.so.0", "libopus.dylib"]
    try:
        import pyogg
        here = os.path.dirname(pyogg.__file__)
        for n in names:
            p = os.path.join(here, n)
            if os.path.exists(p):
                return ctypes.CDLL(p)
    except ImportError:
        pass
    for n in names:  # system libopus (Linux/macOS)
        try:
            return ctypes.CDLL(n)
        except OSError:
            continue
    raise OSError("libopus not found (pip install pyogg)")


class OpusEncoder:
    def __init__(self, sample_rate: int = 48000, channels: int = 2, bitrate: int = 128_000):
        self._lib = _load()
        self._lib.opus_encoder_create.restype = ctypes.c_void_p
        self._lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_void_p, ctypes.c_int]
        self._lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        err = ctypes.c_int()
        self._enc = self._lib.opus_encoder_create(sample_rate, channels, OPUS_APPLICATION_AUDIO, ctypes.byref(err))
        if err.value != 0 or not self._enc:
            raise OSError(f"opus_encoder_create failed ({err.value})")
        enc = ctypes.c_void_p(self._enc)
        self._lib.opus_encoder_ctl(enc, OPUS_SET_BITRATE, ctypes.c_int(bitrate))
        self._lib.opus_encoder_ctl(enc, OPUS_SET_VBR_CONSTRAINT, ctypes.c_int(1))  # steady radio load
        self._lib.opus_encoder_ctl(enc, OPUS_SET_COMPLEXITY, ctypes.c_int(5))      # light on CPU
        self.channels = channels
        self._out = ctypes.create_string_buffer(4000)

    def encode(self, block: np.ndarray) -> bytes:
        """block: float32 (frames, channels) in -1..1; frames must be a valid Opus size (960 = 20 ms)."""
        pcm = np.ascontiguousarray((np.clip(block, -1.0, 1.0) * 32767.0).astype(np.int16))
        n = self._lib.opus_encode(self._enc, pcm.ctypes.data, len(block), self._out, len(self._out))
        if n < 0:
            raise OSError(f"opus_encode failed ({n})")
        return self._out.raw[:n]

    def close(self) -> None:
        if self._enc:
            self._lib.opus_encoder_destroy(self._enc)
            self._enc = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
