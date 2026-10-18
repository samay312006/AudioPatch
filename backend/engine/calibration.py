"""Latency calibration: play a click through each output, find when it is heard,
and suggest per-output delays that line every device up with the slowest one.

Two ways to "hear" the click:
  * mic: hold the headphone next to the laptop mic; a matched filter finds the
    click in the recording to within a millisecond or so.
  * tap: press a key when you hear it. Reaction time is about the same for every
    device, so it cancels out when outputs are compared (less precise).

Only differences between outputs matter, so constant offsets (mic buffering,
reaction time) cancel out.
"""
from __future__ import annotations

import logging
import queue
import random
import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

from backend.engine.devices import OS, thread_init
from backend.engine.mixer import CH, MAX_DELAY_MS, SR

log = logging.getLogger(__name__)

CLICKS_PER_OUTPUT = 5
LISTEN_S = 0.9          # Bluetooth can add 300+ ms; listen long enough after each click
MIN_SNR = 15.0          # correlation peak vs. its noise floor
AGREE_MS = 8.0          # clicks must agree this closely to count as one measurement
MIN_AGREE = 3           # ...and at least this many must agree


def make_click(volume: float = 0.5) -> np.ndarray:
    """A 120 ms rising sweep (0.6->7 kHz). Long and wide-band gives the matched
    filter ~29 dB of processing gain, so it's found even at low volume in a noisy
    room; timing precision comes from the bandwidth, so the peak stays sharp."""
    n = int(SR * 0.12)
    t = np.arange(n) / SR
    f0, f1 = 600.0, 7000.0
    phase = 2 * np.pi * (f0 * t + (f1 - f0) * t ** 2 / (2 * t[-1]))
    fade = int(SR * 0.005)
    env = np.ones(n)
    env[:fade] = np.hanning(2 * fade)[:fade]
    env[-fade:] = np.hanning(2 * fade)[fade:]
    sig = (np.sin(phase) * env * volume).astype(np.float32)
    return np.repeat(sig[:, None], CH, axis=1)


def consensus(samples: list[float]) -> tuple[float, float, int] | None:
    """Median and spread of the largest group of measurements that agree within
    AGREE_MS. Stray detections (noise, a door slam) are outvoted. None if no
    group of MIN_AGREE exists."""
    best: list[float] = []
    for s in samples:
        group = [x for x in samples if abs(x - s) <= AGREE_MS]
        if len(group) > len(best):
            best = group
    if len(best) < MIN_AGREE:
        return None
    return float(np.median(best)), float(np.ptp(best)), len(best)


def find_click(recording: np.ndarray, click: np.ndarray) -> tuple[int, float] | None:
    """Locate `click` (mono template) in a mono recording. Returns (start index,
    snr) or None if nothing stands out from the noise."""
    if len(recording) < len(click):
        return None
    n = len(recording) + len(click)
    nfft = 1 << (n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(recording, nfft) * np.conj(np.fft.rfft(click, nfft)), nfft)
    corr = np.abs(corr[:len(recording) - len(click) + 1])
    peak = int(np.argmax(corr))
    floor = np.median(corr) + 1e-12
    snr = float(corr[peak] / floor)
    if snr < MIN_SNR:
        return None
    return peak, snr


def suggest_delays(latencies: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    """Delay every output so it lines up with the slowest one."""
    if not latencies:
        return {}, []
    slowest = max(latencies.values())
    delays, warnings = {}, []
    for oid, lat in latencies.items():
        d = round(slowest - lat)
        if d > MAX_DELAY_MS:
            warnings.append(oid)
            d = MAX_DELAY_MS
        delays[oid] = float(d)
    return delays, warnings


class MicRecorder:
    """Records the mic with a perf_counter timestamp for every block."""

    def __init__(self, pa_index: int, channels: int):
        self._blocks: list[tuple[float, np.ndarray]] = []
        self._lock = threading.Lock()
        extra = None
        if OS == "windows":
            extra = sd.WasapiSettings(auto_convert=True)
            # Raw mode bypasses Windows voice processing (echo cancellation, noise
            # suppression). Without it a laptop mic array erases the clicks after
            # the first one and adds a variable ~100 ms of processing delay.
            extra._streaminfo.streamOption = sd._lib.eStreamOptionRaw
        self._stream = sd.InputStream(
            device=pa_index, samplerate=SR, channels=max(1, min(2, channels)), dtype="float32",
            latency="low", extra_settings=extra, callback=self._cb,
        )

    def _cb(self, indata, frames, _t, _status):
        # PortAudio's WASAPI input timestamps are unreliable, so stamp blocks with
        # the callback time; the constant offset cancels out between outputs.
        t0 = time.perf_counter() - frames / SR
        with self._lock:
            self._blocks.append((t0, indata.mean(axis=1).copy()))

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *exc):
        try:
            self._stream.abort()
            self._stream.close()
        except Exception:
            pass

    def window(self, t_start: float, t_end: float) -> tuple[float, np.ndarray]:
        """Samples covering [t_start, t_end] and the time of the first one."""
        with self._lock:
            blocks = [(t, b) for t, b in self._blocks if t + len(b) / SR >= t_start and t <= t_end]
            # Drop old blocks so a long session doesn't grow without bound.
            self._blocks = [(t, b) for t, b in self._blocks if t + len(b) / SR >= t_start - 1.0]
        if not blocks:
            return t_start, np.zeros(0, np.float32)
        return blocks[0][0], np.concatenate([b for _, b in blocks])


class Calibration:
    """One calibration run, executed on its own thread. The engine provides
    `get_mixer(output_id)` and `set_calibrating(bool)`; progress goes to `publish`."""

    def __init__(self, outputs: list[str], names: dict[str, str], mode: str,
                 mic: tuple[int, int] | None, get_mixer: Callable, set_calibrating: Callable,
                 publish: Callable[[dict], None]):
        self.outputs = outputs
        self.names = names
        self.mode = mode
        self.mic = mic
        self.get_mixer = get_mixer
        self.set_calibrating = set_calibrating
        self.publish = publish
        self.click = make_click()
        self.taps: queue.Queue[float] = queue.Queue()
        self.cancelled = threading.Event()
        self.result: dict | None = None
        self.thread = threading.Thread(target=self._run, name="calibration", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def tap(self) -> None:
        self.taps.put(time.perf_counter())

    def cancel(self) -> None:
        self.cancelled.set()

    # ------------------------------------------------------------------ run
    def _emit(self, oid: str) -> float | None:
        mixer = self.get_mixer(oid)
        if mixer is None:
            return None
        mixer.play_tone(self.click)
        deadline = time.perf_counter() + 1.0
        while mixer.tone_started_at is None and time.perf_counter() < deadline:
            time.sleep(0.002)
        return mixer.tone_started_at

    def _measure_mic(self, rec: MicRecorder, emitted: float) -> float | None:
        time.sleep(max(0.0, emitted + LISTEN_S - time.perf_counter()))
        t0, samples = rec.window(emitted - 0.05, emitted + LISTEN_S)
        found = find_click(samples, self.click[:, 0])
        if not found:
            return None
        idx, _snr = found
        return (t0 + idx / SR - emitted) * 1000

    def _measure_tap(self, emitted: float) -> float | None:
        try:
            while True:  # discard taps from before this click
                t = self.taps.get(timeout=2.0)
                if t > emitted:
                    return (t - emitted) * 1000
        except queue.Empty:
            return None

    def _run(self) -> None:
        latencies: dict[str, float] = {}
        details: dict[str, dict] = {}
        thread_init()
        self.set_calibrating(True)
        try:
            ctx = MicRecorder(*self.mic) if self.mode == "mic" and self.mic else None
            with ctx or _Null():
                time.sleep(0.3)  # let the mic stream settle
                for i, oid in enumerate(self.outputs):
                    samples = []
                    for k in range(CLICKS_PER_OUTPUT):
                        if self.cancelled.is_set():
                            self.publish({"state": "cancelled"})
                            return
                        self.publish({"state": "running", "output": oid, "index": i,
                                      "total": len(self.outputs), "click": k + 1,
                                      "clicks": CLICKS_PER_OUTPUT})
                        emitted = self._emit(oid)
                        if emitted is None:
                            break
                        ms = self._measure_mic(ctx, emitted) if ctx else self._measure_tap(emitted)
                        if ms is not None:
                            samples.append(ms)
                        time.sleep(random.uniform(0.3, 0.6))  # irregular, so taps can't anticipate
                    agreed = consensus(samples)
                    details[oid] = {
                        "name": self.names.get(oid, oid), "ok": agreed is not None,
                        "latency_ms": round(agreed[0], 1) if agreed else None,
                        "spread_ms": round(agreed[1], 1) if agreed else None,
                        "heard": agreed[2] if agreed else 0, "clicks": CLICKS_PER_OUTPUT,
                    }
                    if agreed:
                        latencies[oid] = agreed[0]
            delays, too_far = suggest_delays(latencies)
            self.result = {"state": "done", "mode": self.mode, "outputs": details,
                           "delays": delays, "clamped": too_far}
            self.publish(self.result)
        except Exception as e:
            log.exception("calibration failed")
            self.publish({"state": "error", "error": str(e)})
        finally:
            self.set_calibrating(False)


class _Null:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
