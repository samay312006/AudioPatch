"""Device-independent mixing. Streams call SourceNode.push() / OutputMixer.render()
from their audio callbacks; everything here is allocation-light and lock-free.

Topology: each (source, output) route owns one RingBuffer. A source writes its
processed block into every routed ring; each output pulls from its rings.

Sync model: a route's latency is how much audio sits in its ring. Each output
holds its rings at `target + delay` frames. Because source and output devices
run on different clocks, the fill slowly drifts; a gentle proportional
controller nudges the read speed (at most +/-0.3%, inaudible) to cancel it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from backend.engine.ringbuffer import RingBuffer

SR = 48000
CH = 2
MAX_DELAY_MS = 500.0


@dataclass
class Params:
    volume: float = 1.0     # linear gain, 0..2
    muted: bool = False
    mono: bool = False      # sources only
    delay_ms: float = 0.0   # outputs only: lip-sync delay

    @property
    def gain(self) -> float:
        return 0.0 if self.muted else self.volume


class LevelMeter:
    """Peak since the last read. Written by the audio thread, read at ~30 Hz."""

    def __init__(self) -> None:
        self._peak = 0.0

    def feed(self, block: np.ndarray) -> None:
        if len(block):
            p = float(np.max(np.abs(block)))
            if p > self._peak:
                self._peak = p

    def take(self) -> float:
        p, self._peak = self._peak, 0.0
        return p


class SourceNode:
    def __init__(self, id: str, params: Params):
        self.id = id
        self.params = params
        self.rings: tuple[RingBuffer, ...] = ()  # replaced atomically by the engine
        self.meter = LevelMeter()

    def push(self, block: np.ndarray) -> None:
        """block: float32 (n, 2) at SR."""
        p = self.params
        if p.mono:
            block = np.repeat(block.mean(axis=1, keepdims=True), CH, axis=1)
        g = p.gain
        if g != 1.0:
            block = block * np.float32(g)
        self.meter.feed(block)
        for ring in self.rings:
            ring.write(block)


class Route:
    """One source feeding one output: a jitter/delay buffer plus drift control."""

    # PI controller on the buffer fill. P reacts (~10 s time constant); I slowly
    # learns the constant clock-rate mismatch so the steady-state error is zero.
    KP = 2e-6        # speed change per frame of fill error
    KI = 1e-9        # per callback (~20 s integral time at 10 ms callbacks)
    MAX_DEV = 0.003  # +/-0.3% speed: about 5 cents of pitch, inaudible
    EMA = 0.02       # fill smoothing per callback (~0.5 s at 10 ms callbacks)

    def __init__(self, source_id: str, output_id: str, seconds: float = 2.0, extra_ms: float = 0.0):
        self.source_id = source_id
        self.output_id = output_id
        # Extra cushion on top of the output's target, e.g. to ride out Wi-Fi jitter
        # from a phone source.
        self.extra_frames = int(SR * extra_ms / 1000)
        self.ring = RingBuffer(int(SR * seconds), CH)
        self.underruns = 0
        self.skips = 0
        self.hold = 0  # frames of silence still to play (a delay increase in progress)
        self.reset()

    def reset(self) -> None:
        self.primed = False
        self.fill_avg: float | None = None
        self.integ = 0.0
        self.step = 1.0
        self._pos = 0.0  # fractional read position; 0 = the frame in _prev
        self._prev = np.zeros((1, CH), np.float32)

    def pull(self, out: np.ndarray, src: np.ndarray, step: float) -> int:
        """Write len(out) frames read at `step` input frames per output frame.
        Returns frames written (< len(out) only on underrun). `src` is scratch."""
        n = len(out)
        if step == 1.0 and self._pos == 0.0:
            got = self.ring.read_into(out)
            if got:
                self._prev[0] = out[got - 1]
            return got
        t = self._pos + step * np.arange(n)
        need = int(np.ceil(t[-1]))  # new frames required beyond _prev
        if need > self.ring.available:
            return 0
        s = src[:need + 1]
        s[0] = self._prev[0]
        self.ring.read_into(s[1:])
        i = t.astype(np.int64)
        f = (t - i).astype(np.float32)[:, None]
        out[:] = s[i] * (1.0 - f) + s[np.minimum(i + 1, need)] * f
        self._pos = max(0.0, t[-1] + step - need)
        self._prev[0] = s[need]
        return n


class OutputMixer:
    def __init__(self, id: str, params: Params, target_ms: float = 40.0):
        self.id = id
        self.params = params
        self.inputs: tuple[Route, ...] = ()  # replaced atomically by the engine
        self.target_frames = int(SR * target_ms / 1000)
        self.meter = LevelMeter()
        self.calibrating = False  # silence routed audio so only test clicks play
        self.tone_started_at: float | None = None  # perf_counter time the tone hit the DAC
        self._delay = 0  # delay currently applied to the routes, in frames
        self._tmp = np.zeros((SR, CH), np.float32)
        self._mix = np.zeros((SR, CH), np.float32)
        self._src = np.zeros((SR + 2, CH), np.float32)
        self._tone: np.ndarray | None = None
        self._tone_pos = 0

    @property
    def slack_frames(self) -> int:
        # How far a buffer may grow past target before we drop audio to catch up.
        return max(self.target_frames, int(SR * 0.08))

    def play_tone(self, tone: np.ndarray) -> None:
        self.tone_started_at = None
        self._tone_pos = 0
        self._tone = tone

    def _apply_delay(self) -> None:
        want = int(SR * min(max(self.params.delay_ms, 0.0), MAX_DELAY_MS) / 1000)
        diff = want - self._delay
        if not diff:
            return
        self._delay = want
        for route in self.inputs:
            if diff > 0:
                route.hold += diff  # play silence while the ring fills up by `diff`
            else:
                route.ring.skip(-diff)
            route.fill_avg = None

    def render(self, n: int, when: float | None = None) -> np.ndarray:
        """Mix n frames. `when` is the perf_counter time the first frame will be heard."""
        if n > len(self._mix):
            self._tmp = np.zeros((n, CH), np.float32)
            self._mix = np.zeros((n, CH), np.float32)
            self._src = np.zeros((int(n * 1.01) + 2, CH), np.float32)
        self._apply_delay()
        mix = self._mix[:n]
        mix.fill(0.0)
        tmp = self._tmp[:n]
        base = self.target_frames + self._delay + n  # fill we hold just before reading

        for route in self.inputs:
            target = base + route.extra_frames
            if route.hold > 0:
                route.hold = max(0, route.hold - n)
                continue
            ring = route.ring
            avail = ring.available
            if not route.primed:
                # Wait until the cushion builds up, so a bursty source (e.g. WASAPI
                # loopback) doesn't crackle; trim any overshoot so latency starts exact.
                if avail < target:
                    continue
                if avail > target:
                    ring.skip(avail - target)
                    avail = target
                route.primed = True
            if avail > target + self.slack_frames:
                ring.skip(avail - target)  # way behind: jump instead of drifting late
                route.skips += 1
                route.fill_avg = None
                avail = target
            # Drift control: steer the smoothed fill toward the target.
            route.fill_avg = avail if route.fill_avg is None else route.fill_avg + Route.EMA * (avail - route.fill_avg)
            err = route.fill_avg - target
            lim = Route.MAX_DEV
            route.integ = min(max(route.integ + Route.KI * err, -lim), lim)
            route.step = 1.0 + min(max(Route.KP * err + route.integ, -lim), lim)
            got = route.pull(tmp, self._src, route.step)
            if got < n:
                route.underruns += 1
                route.reset()
            mix[:got] += tmp[:got]

        if self.calibrating:
            mix.fill(0.0)
        g = self.params.gain
        if g != 1.0:
            mix *= np.float32(g)

        tone = self._tone
        if tone is not None:  # test sounds ignore volume/mute so they're always audible
            if self._tone_pos == 0:
                self.tone_started_at = when if when is not None else time.perf_counter()
            k = min(n, len(tone) - self._tone_pos)
            mix[:k] += tone[self._tone_pos:self._tone_pos + k]
            self._tone_pos += k
            if self._tone_pos >= len(tone):
                self._tone = None

        np.clip(mix, -1.0, 1.0, out=mix)
        self.meter.feed(mix)
        return mix


def make_chime(volume: float = 0.25) -> np.ndarray:
    """Two-note 'ping' used by the per-output Test sound button."""
    def note(freq: float, dur: float) -> np.ndarray:
        t = np.arange(int(SR * dur)) / SR
        env = np.exp(-t * 7.0) * np.minimum(1.0, t / 0.005)
        return (np.sin(2 * np.pi * freq * t) * env).astype(np.float32)

    sig = np.concatenate([note(880.0, 0.18), note(1318.5, 0.45)]) * volume
    return np.repeat(sig[:, None], CH, axis=1)
