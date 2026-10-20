"""AudioEngine: turns the user's routing into running streams.

All stream opening/closing happens on one control thread under self._lock.
Audio callbacks only see tuples the engine swaps in atomically, so they never
block on that lock.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from backend.engine.calibration import Calibration
from backend.engine.devices import DeviceInfo, thread_init
from backend.engine.mixer import OutputMixer, Params, Route, SourceNode, make_chime
from backend.engine.routing import RoutingState
from backend.engine.streams import open_output, open_source

log = logging.getLogger(__name__)

NET_JITTER_MS = 60.0  # extra buffering for audio arriving over Wi-Fi

MODES = {
    # target cushion per route, PortAudio latency hint
    "stable": {"target_ms": 60.0, "latency": "high"},
    "low": {"target_ms": 20.0, "latency": "low"},
}


class AudioEngine:
    def __init__(self, routing: RoutingState, get_devices: Callable[[], list[DeviceInfo]]):
        self.routing = routing
        self.get_devices = get_devices
        self.mode = "stable"
        self.on_status: Callable[[dict], None] = lambda s: None
        self.link = None  # LinkService, set by the app; needed for phone devices

        self._lock = threading.RLock()
        self._params: dict[str, Params] = {}
        self._sources: dict[str, tuple[SourceNode, object, tuple]] = {}   # id -> (node, stream, devkey)
        self._outputs: dict[str, tuple[OutputMixer, object, tuple]] = {}
        self._routes: dict[tuple[str, str], Route] = {}
        self._errors: dict[str, str] = {}
        self._feedback: set[tuple[str, str]] = set()
        self._chime = make_chime()
        self._calib: Calibration | None = None
        self._calibrating = False

        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._worker, name="engine-control", daemon=True)
        self._stop = False
        routing.listeners.append(lambda _snap: self.request_reconcile())

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._thread.start()
        self.request_reconcile()

    def stop(self) -> None:
        self._stop = True
        self._q.put(None)
        with self._lock:
            self._close_all()

    def request_reconcile(self) -> None:
        self._q.put("reconcile")

    def _worker(self) -> None:
        thread_init()
        while not self._stop:
            item = self._q.get()
            # Coalesce bursts (e.g. dragging a slider fires many changes).
            while not self._q.empty():
                item = self._q.get_nowait() or item
            if item is None:
                return
            try:
                with self._lock:
                    self._reconcile()
            except Exception:
                log.exception("reconcile failed")
            self.on_status(self.status())

    def portaudio_guard(self, fn: Callable[[], None]) -> None:
        """Run fn (a PortAudio re-init + rescan) with every stream closed, then
        reopen. PortAudio can't re-enumerate devices with streams open."""
        with self._lock:
            had = bool(self._sources or self._outputs)
            self._close_all()
            try:
                fn()
            finally:
                if had:
                    self._reconcile()
        self.on_status(self.status())

    # --------------------------------------------------------------- params
    def _param(self, dev_id: str, values: dict | None) -> Params:
        p = self._params.setdefault(dev_id, Params())
        if values:  # mutate in place: callbacks hold a reference to this object
            p.volume = float(values.get("volume", p.volume))
            p.muted = bool(values.get("muted", p.muted))
            p.mono = bool(values.get("mono", p.mono))
            p.delay_ms = float(values.get("delay_ms", p.delay_ms))
        return p

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(mode)
        with self._lock:
            if mode != self.mode:
                self.mode = mode
                self._close_all()  # reopen with new latency settings
        self.request_reconcile()

    def test_output(self, dev_id: str) -> bool:
        entry = self._outputs.get(dev_id)
        if not entry:
            return False
        entry[0].play_tone(self._chime)
        return True

    # ---------------------------------------------------------- calibration
    def pick_mic(self) -> DeviceInfo | None:
        """A mic for calibration. Never a Bluetooth headset mic: opening one
        switches the headset to low-quality call mode mid-measurement."""
        rank = {"builtin": 0, "usb": 1, "wired": 2, "unknown": 3}
        mics = [d for d in self.get_devices()
                if d.direction == "input" and d.connected and not d.hands_free
                and d.kind in rank and d.pa_index is not None]
        return min(mics, key=lambda d: rank[d.kind], default=None)

    def start_calibration(self, outputs: list[str], mode: str, publish: Callable[[dict], None]) -> None:
        with self._lock:
            if self._calib and self._calib.thread.is_alive():
                raise RuntimeError("a calibration is already running")
            outs = [o for o in outputs if o in self._outputs]
            if not outs:
                raise ValueError("none of those outputs are running")
            mic = None
            if mode == "mic":
                m = self.pick_mic()
                if not m:
                    raise ValueError("no usable microphone found; use tap mode instead")
                mic = (m.pa_index, m.channels)
            names = {d.id: d.name for d in self.get_devices()}
            self._calib = Calibration(
                outs, names, mode, mic,
                get_mixer=lambda oid: (self._outputs.get(oid) or (None,))[0],
                set_calibrating=self._set_calibrating, publish=publish,
            )
            self._calib.start()

    def calibration_tap(self) -> None:
        if self._calib:
            self._calib.tap()

    def cancel_calibration(self) -> None:
        if self._calib:
            self._calib.cancel()

    @property
    def calibration_result(self) -> dict | None:
        return self._calib.result if self._calib else None

    def _set_calibrating(self, on: bool) -> None:
        self._calibrating = on
        for mixer, _, _ in list(self._outputs.values()):
            mixer.calibrating = on

    # ------------------------------------------------------------ reconcile
    def _reconcile(self) -> None:
        snap = self.routing.snapshot()
        devs = {d.id: d for d in self.get_devices()}
        mode = MODES[self.mode]
        for dev_id, values in snap["params"].items():
            self._param(dev_id, values)

        def wanted(ids: list[str]) -> dict[str, DeviceInfo]:
            return {i: devs[i] for i in ids if i in devs and devs[i].connected}

        want_out, want_src = wanted(snap["outputs"]), wanted(snap["sources"])

        # Outputs: close stale, open missing.
        for oid in list(self._outputs):
            if oid not in want_out or self._outputs[oid][2] != _devkey(want_out[oid]):
                self._close(self._outputs.pop(oid)[1])
        for oid, dev in want_out.items():
            if oid in self._outputs:
                continue
            mixer = OutputMixer(oid, self._param(oid, None), mode["target_ms"])
            mixer.calibrating = self._calibrating
            stream = self._open(dev, lambda: open_output(dev, mixer, mode["latency"], self.link))
            if stream:
                self._outputs[oid] = (mixer, stream, _devkey(dev))

        for sid in list(self._sources):
            if sid not in want_src or self._sources[sid][2] != _devkey(want_src[sid]):
                self._close(self._sources.pop(sid)[1])
        for sid, dev in want_src.items():
            if sid in self._sources:
                continue
            node = SourceNode(sid, self._param(sid, None))
            stream = self._open(dev, lambda: open_source(dev, node, mode["latency"], self.link))
            if stream:
                self._sources[sid] = (node, stream, _devkey(dev))

        # Routes between running endpoints, minus feedback loops.
        self._feedback = set()
        live: dict[tuple[str, str], Route] = {}
        for sid, outs in snap["routes"].items():
            if sid not in self._sources:
                continue
            captures = want_src[sid].extra.get("captures")
            for oid in outs:
                if oid not in self._outputs:
                    continue
                if captures and want_out[oid].name == captures:
                    self._feedback.add((sid, oid))  # would play back into itself
                    continue
                extra = NET_JITTER_MS if want_src[sid].kind == "network" else 0.0
                live[(sid, oid)] = self._routes.get((sid, oid)) or Route(sid, oid, extra_ms=extra)
        self._routes = live

        # Swap in new topology. Callbacks read these tuples without locking.
        for oid, (mixer, _, _) in self._outputs.items():
            mixer.inputs = tuple(r for (s, o), r in live.items() if o == oid)
        for sid, (node, _, _) in self._sources.items():
            node.rings = tuple(r.ring for (s, o), r in live.items() if s == sid)

    def _open(self, dev: DeviceInfo, factory):
        try:
            stream = factory()
            stream.start()
            self._errors.pop(dev.id, None)
            log.info("opened %s", dev.name)
            return stream
        except Exception as e:
            self._errors[dev.id] = f"Couldn't open {dev.name}: {e}"
            log.warning("open failed for %s: %s", dev.name, e)
            return None

    def _close(self, stream) -> None:
        try:
            stream.close()
        except Exception:
            log.debug("close failed", exc_info=True)

    def _close_all(self) -> None:
        for _, stream, _ in list(self._outputs.values()) + list(self._sources.values()):
            self._close(stream)
        self._outputs.clear()
        self._sources.clear()

    # --------------------------------------------------------------- status
    @property
    def has_streams(self) -> bool:
        return bool(self._outputs or self._sources)

    def levels(self) -> dict[str, float]:
        out = {i: round(e[0].meter.take(), 4) for i, e in list(self._outputs.items())}
        out.update({i: round(e[0].meter.take(), 4) for i, e in list(self._sources.items())})
        return out

    def status(self) -> dict:
        with self._lock:
            devices = {}
            for oid, (mixer, stream, _) in self._outputs.items():
                devices[oid] = {
                    "running": True, "latency_ms": getattr(stream, "latency_ms", None),
                    "xruns": stream.xruns, "error": stream.error,
                    "underruns": sum(r.underruns for r in mixer.inputs),
                }
            for sid, (_, stream, _) in self._sources.items():
                devices[sid] = {"running": True, "xruns": stream.xruns, "error": stream.error}
            for dev_id, err in self._errors.items():
                devices.setdefault(dev_id, {"running": False})["error"] = err
            return {
                "mode": self.mode,
                "devices": devices,
                "feedback": [{"source": s, "output": o} for s, o in sorted(self._feedback)],
            }


def _devkey(d: DeviceInfo) -> tuple:
    """If any of these change, the stream must be reopened."""
    return (d.pa_index, d.channels, d.extra.get("pyaudio_index"), d.extra.get("captures"))
