"""User-chosen sources, outputs, and the source->output matrix.

Pure state with a lock: API handlers mutate it, the audio engine reads
snapshot() and never holds the lock in a callback.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)


class RoutingState:
    def __init__(self, path: Path | None = None):
        self._lock = threading.Lock()
        self.path = path
        self.sources: list[str] = []          # device ids, in display order
        self.outputs: list[str] = []
        self.routes: dict[str, set[str]] = {}  # source id -> output ids
        self.names: dict[str, str] = {}       # last known name, for offline devices
        self.params: dict[str, dict] = {}     # id -> {volume, muted, mono}
        self.listeners: list = []
        self.load()

    # ---------------------------------------------------------------- edits
    def add_source(self, dev_id: str, name: str = "") -> None:
        with self._lock:
            if dev_id not in self.sources:
                self.sources.append(dev_id)
                # A new source plays to every current output by default.
                self.routes[dev_id] = set(self.outputs)
            if name:
                self.names[dev_id] = name
        self._changed()

    def remove_source(self, dev_id: str) -> None:
        with self._lock:
            if dev_id in self.sources:
                self.sources.remove(dev_id)
            self.routes.pop(dev_id, None)
        self._changed()

    def add_output(self, dev_id: str, name: str = "") -> None:
        with self._lock:
            if dev_id not in self.outputs:
                self.outputs.append(dev_id)
                # Every existing source starts routed to a new output.
                for outs in self.routes.values():
                    outs.add(dev_id)
            if name:
                self.names[dev_id] = name
        self._changed()

    def remove_output(self, dev_id: str) -> None:
        with self._lock:
            if dev_id in self.outputs:
                self.outputs.remove(dev_id)
            for outs in self.routes.values():
                outs.discard(dev_id)
        self._changed()

    def set_route(self, source: str, output: str, on: bool) -> None:
        with self._lock:
            if source not in self.sources or output not in self.outputs:
                raise KeyError("source and output must both be added first")
            outs = self.routes.setdefault(source, set())
            outs.add(output) if on else outs.discard(output)
        self._changed()

    PARAM_KEYS = {"volume": float, "muted": bool, "mono": bool,
                  "delay_ms": float, "measured_ms": float}
    CLAMP = {"volume": (0.0, 2.0), "delay_ms": (0.0, 1000.0)}

    def set_params(self, dev_id: str, **values) -> None:
        with self._lock:
            if dev_id not in self.sources and dev_id not in self.outputs:
                raise KeyError("add the device before changing its settings")
            p = self.params.setdefault(dev_id, {})
            for k, v in values.items():
                if k not in self.PARAM_KEYS or v is None:
                    continue
                v = self.PARAM_KEYS[k](v)
                if k in self.CLAMP:
                    lo, hi = self.CLAMP[k]
                    v = min(max(v, lo), hi)
                p[k] = v
        self._changed()

    # ----------------------------------------------------------------- read
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "sources": list(self.sources),
                "outputs": list(self.outputs),
                "routes": {s: sorted(o) for s, o in self.routes.items()},
                "names": dict(self.names),
                "params": {k: dict(v) for k, v in self.params.items()},
            }

    # ---------------------------------------------------------- persistence
    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.sources = list(data.get("sources", []))
            self.outputs = list(data.get("outputs", []))
            self.names = dict(data.get("names", {}))
            self.params = {k: dict(v) for k, v in data.get("params", {}).items()}
            self.routes = {s: set(o) & set(self.outputs)
                           for s, o in data.get("routes", {}).items() if s in self.sources}
        except Exception:
            log.exception("could not read %s; starting with an empty setup", self.path)

    def _changed(self) -> None:
        snap = self.snapshot()
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(snap, indent=2), encoding="utf-8")
                tmp.replace(self.path)
            except Exception:
                log.exception("could not save routing")
        for fn in self.listeners:
            fn(snap)
