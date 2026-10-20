"""Output stream for a phone connected over Bluetooth (mirrors engine.streams).

There's no sound card on this side, so a thread paces the mixer with the PC's
clock: one 20 ms Opus frame per tick. The phone's jitter buffer and drift
control absorb Bluetooth jitter and the difference between the two clocks.
"""
from __future__ import annotations

import logging
import threading
import time

from backend.bt import protocol as P
from backend.bt.opus import OpusEncoder
from backend.bt.service import LinkCore
from backend.engine.devices import DeviceInfo
from backend.engine.mixer import SR, OutputMixer

log = logging.getLogger(__name__)

STALL_RESET_S = 0.25  # starved this long: don't burst out a backlog


class BtOutputStream:
    def __init__(self, dev: DeviceInfo, mixer: OutputMixer, core: LinkCore):
        if not core.has(dev.id):
            raise RuntimeError(f"{dev.name} is no longer connected")
        self.dev, self.mixer, self.core = dev, mixer, core
        self.encoder = OpusEncoder(P.SAMPLE_RATE, P.CHANNELS, P.BITRATE)
        self.error: str | None = None
        self.xruns = 0
        self.latency_ms = None  # unknown until calibrated (Bluetooth + phone + its headphones)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"bt-out-{dev.name}", daemon=True)

    def start(self) -> None:
        self.core.set_streaming(self.dev.id, True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self.core.set_streaming(self.dev.id, False)
        self.encoder.close()

    def _run(self) -> None:
        period = P.FRAME_SAMPLES / SR
        nxt = time.perf_counter()
        seq = 0
        while not self._stop.is_set():
            now = time.perf_counter()
            if now < nxt:
                time.sleep(nxt - now)
                continue
            if now - nxt > STALL_RESET_S:
                self.xruns += 1
                nxt = now
            try:
                block = self.mixer.render(P.FRAME_SAMPLES, when=nxt)
                if not self.core.send_audio(self.dev.id, P.audio(seq, self.encoder.encode(block))):
                    self.xruns += 1
            except Exception as e:  # one phone must never take the engine down
                self.error = repr(e)
                log.exception("bluetooth output failed")
            seq += 1
            nxt += period
