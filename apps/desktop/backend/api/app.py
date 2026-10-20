"""FastAPI app: REST for state/settings, one WebSocket for live events."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.engine import devices as dev
from backend.engine.engine import MODES, AudioEngine
from backend.engine.routing import RoutingState
from backend.bt.service import BluetoothService

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "frontend" / "dist"
FROZEN = getattr(sys, "frozen", False)
# The packaged app keeps settings in %APPDATA%\AudioPatch; dev runs use ./presets.
PRESETS = Path(os.environ.get("AUDIOPATCH_DATA") or (
    Path(os.environ.get("APPDATA", Path.home())) / "AudioPatch" if FROZEN else ROOT / "presets"))
LEVEL_HZ = 30


class Hub:
    """Fan-out of engine events to WebSocket clients. Engine threads call
    publish_threadsafe(); they never await or touch sockets directly."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        # For "quit when the window closes": when did the last client leave?
        self.ever_connected = False
        self.idle_since = time.monotonic()

    def add(self, ws: WebSocket) -> None:
        self.clients.add(ws)
        self.ever_connected = True

    def remove(self, ws: WebSocket) -> None:
        self.clients.discard(ws)
        if not self.clients:
            self.idle_since = time.monotonic()

    def publish_threadsafe(self, msg: dict) -> None:
        if self.loop and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.broadcast(msg), self.loop)

    async def broadcast(self, msg: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                self.remove(ws)


def is_vbcable(d: dev.DeviceInfo) -> bool:
    return "vb-audio virtual cable" in d.name.lower()


def visible(ds: list[dev.DeviceInfo]) -> list[dict]:
    """Devices usable right now, plus any the user has added that are currently
    disconnected (shown greyed out). Hidden: paired-but-absent Bluetooth devices,
    hands-free (HFP) duplicates, and the VB-Cable endpoints (plumbing, not devices)."""
    snap = routing.snapshot()
    chosen = set(snap["sources"]) | set(snap["outputs"])
    return [d.to_dict() for d in ds
            if (d.connected or d.id in chosen) and not d.hands_free and not is_vbcable(d)]


hub = Hub()
routing = RoutingState(PRESETS / os.environ.get("AUDIOPATCH_SESSION", "session.json"))


def all_devices() -> list[dev.DeviceInfo]:
    """Local sound devices plus phones connected over Bluetooth."""
    return watcher.devices + bt.core.devices


def _on_devices(_ds=None) -> None:
    hub.publish_threadsafe({"type": "devices", "devices": visible(all_devices())})
    hub.publish_threadsafe({"type": "system", "system": system_info()})
    engine.request_reconcile()


watcher = dev.DeviceWatcher(on_change=_on_devices)
bt = BluetoothService(on_change=_on_devices, pc_name=socket.gethostname(),
                      memory_file=PRESETS / "phones.json",
                      wanted=lambda: set(routing.snapshot()["outputs"]))
engine = AudioEngine(routing, all_devices)
engine.bt_core = bt.core
watcher.reinit_guard = engine.portaudio_guard
watcher.busy = lambda: engine.has_streams
engine.on_status = lambda s: hub.publish_threadsafe({"type": "engine", "engine": s})


def _on_routing(snap: dict) -> None:
    hub.publish_threadsafe({"type": "routing", "routing": snap})


routing.listeners.append(_on_routing)


def system_info() -> dict:
    info = dev.system_info(watcher.devices)
    info["bluetooth"] = {"running": bt.running, "error": bt.error, "name": bt.core.pc_name}
    cables = [d for d in watcher.devices if is_vbcable(d) and d.direction == "output" and d.connected]
    # VB-Cable also installs a 16-channel "CABLE In 16ch"; prefer the stereo one.
    cable = next((d for d in cables if d.name.startswith("CABLE Input")), cables[0] if cables else None)
    loop = next((d for d in watcher.devices if d.kind == "loopback"), None)
    default_out = loop.extra.get("captures") if loop else None
    info.update({
        "default_output": default_out,
        "virtual_cable": {
            "installed": cable is not None,
            "output_id": cable.id if cable else None,
            "is_default": any(default_out == c.name for c in cables),
        },
    })
    return info


async def _level_pump() -> None:
    """~30 Hz meter stream. Reads peaks the audio threads left behind."""
    while True:
        await asyncio.sleep(1 / LEVEL_HZ)
        if hub.clients and engine.has_streams:
            await hub.broadcast({"type": "levels", "levels": engine.levels()})


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    watcher.start()
    if os.environ.get("AUDIOPATCH_BT", "1") != "0":
        await asyncio.to_thread(bt.start)
    engine.start()
    pump = asyncio.create_task(_level_pump())
    yield
    pump.cancel()
    watcher.stop()
    bt.stop()
    engine.stop()


app = FastAPI(title="AudioPatch", lifespan=lifespan)


class DeviceRef(BaseModel):
    id: str


class RouteToggle(BaseModel):
    source: str
    output: str
    on: bool


class ParamsBody(BaseModel):
    id: str
    volume: float | None = None
    muted: bool | None = None
    mono: bool | None = None
    delay_ms: float | None = None


class ModeBody(BaseModel):
    mode: str


class CalibrateBody(BaseModel):
    mode: str = "mic"
    outputs: list[str] | None = None  # default: every running output


def _find(dev_id: str, direction: str) -> dev.DeviceInfo:
    for d in all_devices():
        if d.id == dev_id and d.direction == direction and not d.hands_free:
            if not d.connected:
                raise HTTPException(409, f"{d.name} is not connected")
            return d
    raise HTTPException(404, f"no connected {direction} device with that id")


def _push_devices() -> None:
    # Removing a disconnected device should make it vanish from the list.
    hub.publish_threadsafe({"type": "devices", "devices": visible(all_devices())})


# --------------------------------------------------------------- read

@app.get("/api/system")
def get_system():
    return system_info()


@app.get("/api/devices")
def get_devices(all: bool = False):
    """?all=true includes paired/absent devices, for debugging."""
    return [d.to_dict() for d in all_devices()] if all else visible(all_devices())


@app.get("/api/routing")
def get_routing():
    return routing.snapshot()


@app.get("/api/engine")
def get_engine():
    return engine.status()


# -------------------------------------------------------------- setup

@app.post("/api/sources")
def add_source(body: DeviceRef):
    d = _find(body.id, "input")
    routing.add_source(d.id, d.name)
    _push_devices()
    return routing.snapshot()


@app.post("/api/sources/remove")
def remove_source(body: DeviceRef):
    routing.remove_source(body.id)
    _push_devices()
    return routing.snapshot()


@app.post("/api/outputs")
def add_output(body: DeviceRef):
    d = _find(body.id, "output")
    if d.kind == "phone" and any(p["output"]["device_id"] == d.id and p["audio"]["connected"] for p in _phones()):
        raise HTTPException(409, f"{d.name} is sending its own audio to this PC; playing the PC's sound "
                                 "on it too would loop forever. Stop its phone audio first.")
    routing.add_output(d.id, d.name)
    _push_devices()
    return routing.snapshot()


@app.post("/api/outputs/remove")
def remove_output(body: DeviceRef):
    routing.remove_output(body.id)
    _push_devices()
    return routing.snapshot()


@app.post("/api/routes")
def set_route(body: RouteToggle):
    try:
        routing.set_route(body.source, body.output, body.on)
    except KeyError as e:
        raise HTTPException(400, str(e))
    return routing.snapshot()


@app.post("/api/params")
def set_params(body: ParamsBody):
    try:
        routing.set_params(body.id, **body.model_dump(exclude={"id"}, exclude_none=True))
    except KeyError as e:
        raise HTTPException(400, str(e))
    return routing.snapshot()


@app.post("/api/outputs/test")
def test_output(body: DeviceRef):
    if not engine.test_output(body.id):
        raise HTTPException(409, "that output isn't running; add it and make sure it's connected")
    return {"ok": True}


@app.post("/api/engine/mode")
def set_mode(body: ModeBody):
    if body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {sorted(MODES)}")
    engine.set_mode(body.mode)
    return {"mode": body.mode}


# ------------------------------------------------------ phones (Bluetooth)

def _bt_call(coro, timeout: float = 30):
    try:
        return bt.call(coro, timeout)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except Exception as e:  # WinRT errors, timeouts
        raise HTTPException(502, f"Bluetooth: {e}")


def _phones() -> list[dict]:
    try:
        return bt.call(bt.phones(), 15) if bt.running else []
    except Exception:
        return []


@app.get("/api/bt/phones")
def bt_phones():
    """Paired phones, with the state of both phone features."""
    return _bt_call(bt.phones())


@app.post("/api/bt/phones/output")
def bt_output_connect(body: DeviceRef):
    """Connect to the app on this phone so it can be used as an output."""
    _bt_call(bt.connect_output(body.id), 45)
    return _bt_call(bt.phones())


@app.post("/api/bt/phones/output/disconnect")
def bt_output_disconnect(body: DeviceRef):
    _bt_call(bt.disconnect_output(body.id))
    return _bt_call(bt.phones())


@app.post("/api/bt/phones/audio")
def bt_audio_connect(body: DeviceRef):
    """Let this phone play its own audio through the PC (PC as Bluetooth speaker)."""
    phone = next((p for p in _phones() if p["audio"]["id"] == body.id), None)
    if phone and phone["output"]["device_id"] in routing.snapshot()["outputs"]:
        raise HTTPException(409, f"{phone['name']} is one of your outputs; sending its audio here too "
                                 "would loop forever. Remove it from Outputs first.")
    _bt_call(bt.connect_audio(body.id), 45)
    return _bt_call(bt.phones())


@app.post("/api/bt/phones/audio/disconnect")
def bt_audio_disconnect(body: DeviceRef):
    _bt_call(bt.disconnect_audio(body.id))
    return _bt_call(bt.phones())


# -------------------------------------------------------- calibration

def _publish_calibration(msg: dict) -> None:
    hub.publish_threadsafe({"type": "calibration", "calibration": msg})


@app.get("/api/calibrate/mic")
def calibration_mic():
    m = engine.pick_mic()
    return {"id": m.id, "name": m.name} if m else {"id": None, "name": None}


@app.post("/api/calibrate/start")
def calibrate_start(body: CalibrateBody):
    if body.mode not in ("mic", "tap"):
        raise HTTPException(400, "mode must be mic or tap")
    outputs = body.outputs or routing.snapshot()["outputs"]
    try:
        engine.start_calibration(outputs, body.mode, _publish_calibration)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(409, str(e))
    names = {d.id: d.name for d in all_devices()}
    quiet = []
    for oid in outputs:
        v = dev.endpoint_volume(oid)
        if v is not None and v < 0.15:
            quiet.append({"id": oid, "name": names.get(oid, oid), "volume": round(v * 100)})
    return {"ok": True, "quiet": quiet}


@app.post("/api/calibrate/tap")
def calibrate_tap():
    engine.calibration_tap()
    return {"ok": True}


@app.post("/api/calibrate/cancel")
def calibrate_cancel():
    engine.cancel_calibration()
    return {"ok": True}


@app.post("/api/calibrate/apply")
def calibrate_apply():
    res = engine.calibration_result
    if not res or res.get("state") != "done" or not res["delays"]:
        raise HTTPException(409, "no finished calibration to apply")
    for oid, delay in res["delays"].items():
        if oid in routing.snapshot()["outputs"]:
            lat = res["outputs"][oid]["latency_ms"]
            # Tap-mode numbers include reaction time, so only store mic measurements.
            routing.set_params(oid, delay_ms=delay, measured_ms=lat if res["mode"] == "mic" else None)
    return routing.snapshot()


@app.post("/api/system/use-virtual-cable")
def use_virtual_cable():
    """Make VB-Cable the Windows default output, so apps play silently into it
    and AudioPatch is the only thing feeding your real devices."""
    if dev.OS != "windows":
        raise HTTPException(400, "only needed on Windows")
    info = system_info()["virtual_cable"]
    if not info["installed"]:
        raise HTTPException(404, "VB-Cable isn't installed")
    import comtypes
    from pycaw.constants import ERole
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        AudioUtilities.SetDefaultDevice(info["output_id"], roles=[ERole.eConsole, ERole.eMultimedia])
    finally:
        comtypes.CoUninitialize()
    return {"ok": True}  # the watcher notices the default change and rescans


# ---------------------------------------------------------- websocket

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    hub.add(ws)
    await ws.send_json({"type": "devices", "devices": visible(all_devices())})
    await ws.send_json({"type": "routing", "routing": routing.snapshot()})
    await ws.send_json({"type": "system", "system": system_info()})
    await ws.send_json({"type": "engine", "engine": engine.status()})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(ws)


if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")
