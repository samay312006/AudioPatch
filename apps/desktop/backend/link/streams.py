"""Stream wrappers for phones, mirroring backend.engine.streams.

A phone output has no sound card clock on this side, so a thread paces the
mixer at exactly 48 kHz using the PC's clock; the phone's jitter buffer and
drift control absorb Wi-Fi jitter and the difference between the two clocks.
"""
from __future__ import annotations

import logging
import threading
import time

from backend.engine.devices import DeviceInfo
from backend.engine.mixer import SR, OutputMixer, SourceNode
from backend.link import protocol as P
from backend.link.service import LinkService

log = logging.getLogger(__name__)

STALL_RESET_S = 0.2  # if the thread was starved this long, don't burst out a backlog


class NetworkOutputStream:
    def __init__(self, dev: DeviceInfo, mixer: OutputMixer, link: LinkService):
        session = link.session(dev.id)
        if not session:
            raise RuntimeError(f"{dev.name} is no longer connected")
        self.dev, self.mixer, self.link = dev, mixer, link
        self.stream_id = session.stream_id
        self.error: str | None = None
        self.xruns = 0
        self.latency_ms = None  # unknown until calibrated: Wi-Fi + phone + its headphones
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"net-out-{dev.name}", daemon=True)

    def start(self) -> None:
        self.link.set_streaming(self.dev.id, True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self.link.set_streaming(self.dev.id, False)

    def _run(self) -> None:
        period = P.PACKET_FRAMES / SR
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
                block = self.mixer.render(P.PACKET_FRAMES, when=nxt)
                self.link.send_audio(self.dev.id, P.pack_audio(self.stream_id, seq, block))
            except Exception as e:  # never let one phone take the engine down
                self.error = repr(e)
                log.exception("network output failed")
            seq += 1
            nxt += period


class NetworkSourceStream:
    def __init__(self, dev: DeviceInfo, node: SourceNode, link: LinkService):
        session = link.session(dev.id)
        if not session:
            raise RuntimeError(f"{dev.name} is no longer connected")
        self.dev, self.node, self.link = dev, node, link
        self.stream_id = session.stream_id
        self.error: str | None = None
        self.xruns = 0

    def start(self) -> None:
        self.link.register_sink(self.stream_id, self.node.push)

    def close(self) -> None:
        self.link.unregister_sink(self.stream_id)
