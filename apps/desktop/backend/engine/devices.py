"""Device discovery: merges PortAudio (what we can open) with OS metadata
(what kind of device it is, and which paired devices are currently absent).

Nothing in here touches the network or UI; the API layer subscribes to
DeviceWatcher callbacks.
"""
from __future__ import annotations

import logging
import platform
import threading
import warnings
from dataclasses import asdict, dataclass, field
from typing import Callable, Literal

import sounddevice as sd

log = logging.getLogger(__name__)

OS = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")

DeviceKind = Literal["bluetooth", "usb", "hdmi", "wired", "builtin", "virtual", "loopback", "unknown"]
Direction = Literal["input", "output"]

# HDAUDIO vendor IDs that belong to GPUs, i.e. HDMI / DisplayPort audio.
_GPU_VENDORS = ("VEN_10DE", "VEN_1002", "VEN_8086&DEV_280", "VEN_8086&DEV_2812")
_VIRTUAL_HINTS = ("blackhole", "virtual", "vb-audio", "voicemeeter", "cable", "steam streaming", "soundflower")
_PKEY_INSTANCE_ID = "{B3F8FA53-0004-438E-9003-51A46E139BFC} 2"


@dataclass
class DeviceInfo:
    id: str                      # stable across reconnects (endpoint GUID on Windows)
    name: str
    direction: Direction
    kind: DeviceKind
    connected: bool
    channels: int = 0
    samplerate: float = 48000.0
    latency_ms: float = 0.0      # PortAudio's reported default-low latency
    pa_index: int | None = None  # index into sounddevice (None if not openable now)
    hands_free: bool = False     # Bluetooth HFP profile: mono, 16 kHz, hidden by default
    is_default: bool = False
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def classify(name: str, instance_id: str = "") -> tuple[DeviceKind, bool]:
    """Return (kind, is_hands_free) from a friendly name and OS instance id."""
    iid = instance_id.upper()
    lname = name.lower()
    if iid.startswith("{1}.BTHHFENUM") or "hands-free" in lname:
        return "bluetooth", True
    if "BTHENUM" in iid or "BTHLEDEVICE" in iid or "bluetooth" in lname:
        return "bluetooth", False
    if any(h in lname for h in _VIRTUAL_HINTS):
        return "virtual", False
    if "loopback" in lname:
        return "loopback", False
    if "USB" in iid.split("\\")[0] or "usb" in lname:
        return "usb", False
    if "HDAUDIO" in iid and any(v in iid for v in _GPU_VENDORS):
        return "hdmi", False
    if "hdmi" in lname or "displayport" in lname or "nvidia high definition" in lname:
        return "hdmi", False
    if "headphone" in lname or "line" in lname or "headset" in lname:
        return "wired", False
    if "HDAUDIO" in iid or "INTELAUDIO" in iid or any(w in lname for w in ("speaker", "microphone", "built-in", "macbook")):
        return "builtin", False
    return "unknown", False


# --------------------------------------------------------------------- Windows

def _pa_wasapi_index() -> int | None:
    for i, h in enumerate(sd.query_hostapis()):
        if "WASAPI" in h["name"]:
            return i
    return None


def _scan_windows() -> list[DeviceInfo]:
    from pycaw.pycaw import AudioUtilities

    api = _pa_wasapi_index()
    pa_by_name: dict[tuple[str, str], dict] = {}
    for d in sd.query_devices():
        if d["hostapi"] != api:
            continue
        if d["max_output_channels"] > 0:
            pa_by_name[(d["name"], "output")] = d
        if d["max_input_channels"] > 0:
            pa_by_name[(d["name"], "input")] = d

    try:
        default_out = sd.query_devices(kind="output")["name"]
        default_in = sd.query_devices(kind="input")["name"]
    except Exception:
        default_out = default_in = ""

    out: list[DeviceInfo] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        endpoints = AudioUtilities.GetAllDevices()

    seen_names: set[tuple[str, str]] = set()
    for ep in endpoints:
        name = ep.FriendlyName
        if not name:
            continue
        direction: Direction = "input" if ep.id.startswith("{0.0.1.") else "output"
        state = ep.state.name  # Active / Disabled / NotPresent / Unplugged
        if state in ("Disabled", "NotPresent"):
            continue  # NotPresent = hardware gone (old HDMI monitors etc.)
        key = (name, direction)
        if key in seen_names:
            continue
        seen_names.add(key)
        iid = str((ep.properties or {}).get(_PKEY_INSTANCE_ID, ""))
        kind, hf = classify(name, iid)
        pa = pa_by_name.get(key)
        connected = state == "Active" and pa is not None
        out.append(DeviceInfo(
            id=ep.id,
            name=name,
            direction=direction,
            kind=kind,
            connected=connected,
            hands_free=hf,
            channels=(pa["max_output_channels"] if direction == "output" else pa["max_input_channels"]) if pa else 0,
            samplerate=pa["default_samplerate"] if pa else 48000.0,
            latency_ms=round((pa["default_low_output_latency"] if direction == "output" else pa["default_low_input_latency"]) * 1000, 1) if pa else 0.0,
            pa_index=pa["index"] if pa else None,
            is_default=name == (default_out if direction == "output" else default_in),
            extra={"state": state},
        ))

    out.extend(_windows_loopbacks())
    return out


def _windows_loopbacks() -> list[DeviceInfo]:
    """System-audio capture. PortAudio via sounddevice can't do WASAPI loopback,
    so these are opened through PyAudioWPatch."""
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        return []
    # Exposed as ONE source capturing the Windows default output. Per-device
    # loopbacks would let users feed an output back into itself.
    p = pyaudio.PyAudio()
    try:
        d = p.get_default_wasapi_loopback()
    except Exception:
        return []
    finally:
        p.terminate()
    return [DeviceInfo(
        id="loopback:system",
        name="System audio",
        direction="input",
        kind="loopback",
        connected=True,
        channels=d["maxInputChannels"],
        samplerate=d["defaultSampleRate"],
        extra={"pyaudio_index": d["index"], "backend": "pyaudiowpatch",
               "captures": d["name"].replace(" [Loopback]", "")},
    )]


def _windows_active_ids() -> frozenset[str]:
    """Cheap change detector: only endpoint IDs + state, no property reads."""
    from pycaw.pycaw import AudioUtilities
    from pycaw.constants import DEVICE_STATE, EDataFlow

    enum = AudioUtilities.GetDeviceEnumerator()
    coll = enum.EnumAudioEndpoints(EDataFlow.eAll.value, DEVICE_STATE.ACTIVE.value)
    ids = {coll.Item(i).GetId() for i in range(coll.GetCount())}
    for flow in (EDataFlow.eRender.value, EDataFlow.eCapture.value):
        try:  # default changes alter what "System audio" captures
            ids.add("default:" + enum.GetDefaultAudioEndpoint(flow, 0).GetId())
        except Exception:
            pass
    return frozenset(ids)


# ----------------------------------------------------------------- macOS/other

def _scan_portaudio() -> list[DeviceInfo]:
    try:
        default_in, default_out = sd.default.device
    except Exception:
        default_in = default_out = -1
    out: list[DeviceInfo] = []
    for d in sd.query_devices():
        for direction, ch_key, lat_key, default in (
            ("output", "max_output_channels", "default_low_output_latency", default_out),
            ("input", "max_input_channels", "default_low_input_latency", default_in),
        ):
            if d[ch_key] <= 0:
                continue
            kind, hf = classify(d["name"])
            out.append(DeviceInfo(
                id=f"{direction}:{d['name']}",
                name=d["name"],
                direction=direction,  # type: ignore[arg-type]
                kind=kind,
                connected=True,
                channels=d[ch_key],
                samplerate=d["default_samplerate"],
                latency_ms=round(d[lat_key] * 1000, 1),
                pa_index=d["index"],
                hands_free=hf,
                is_default=d["index"] == default,
            ))
    return out


# --------------------------------------------------------------------- public

def thread_init() -> None:
    """Call at the start of any thread that opens audio streams: PortAudio's
    WASAPI backend needs COM initialised on the opening thread."""
    if OS == "windows":
        import comtypes
        comtypes.CoInitialize()


def _endpoint(dev_id: str):
    thread_init()
    from pycaw.pycaw import AudioUtilities
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return AudioUtilities.CreateDevice(AudioUtilities.GetDeviceEnumerator().GetDevice(dev_id)).EndpointVolume


def endpoint_volume(dev_id: str) -> float | None:
    """Windows' own volume for an output (0..1, 0 if muted), or None if unknown or
    not a Windows endpoint. Matters because with VB-Cable as the default output,
    the keyboard volume keys no longer change real devices' volumes."""
    if OS != "windows" or not dev_id.startswith("{"):
        return None
    try:
        v = _endpoint(dev_id)
        return 0.0 if v.GetMute() else float(v.GetMasterVolumeLevelScalar())
    except Exception:
        log.debug("couldn't read endpoint volume", exc_info=True)
        return None


def set_endpoint_volume(dev_id: str, level: float) -> bool:
    """Set (and unmute) Windows' own volume for an output."""
    if OS != "windows" or not dev_id.startswith("{"):
        return False
    try:
        v = _endpoint(dev_id)
        v.SetMute(0, None)
        v.SetMasterVolumeLevelScalar(min(max(level, 0.0), 1.0), None)
        return True
    except Exception:
        log.warning("couldn't set endpoint volume", exc_info=True)
        return False


def refresh_portaudio() -> None:
    """PortAudio caches its device list at init; re-init to see hot-plugged devices.
    Callers must ensure no streams are open (the engine handles this)."""
    sd._terminate()
    sd._initialize()


def scan() -> list[DeviceInfo]:
    if OS == "windows":
        return _scan_windows()
    return _scan_portaudio()


def system_info(devices: list[DeviceInfo]) -> dict:
    info = {
        "os": OS,
        "os_version": platform.version(),
        "python": platform.python_version(),
        "portaudio": sd.get_portaudio_version()[1],
        "loopback_available": any(d.kind == "loopback" for d in devices),
        "blackhole_installed": None,
    }
    if OS == "macos":
        info["blackhole_installed"] = any("blackhole" in d.name.lower() for d in devices)
        info["loopback_available"] = info["blackhole_installed"]
    return info


class DeviceWatcher:
    """Background poller. On Windows it watches the cheap active-ID set and only
    rescans fully on change; elsewhere it re-inits PortAudio periodically."""

    def __init__(self, on_change: Callable[[list[DeviceInfo]], None], interval: float = 1.5):
        self.on_change = on_change
        self.interval = interval
        self.devices: list[DeviceInfo] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="device-watcher", daemon=True)
        # The engine replaces this to close its streams around a PortAudio re-init.
        self.reinit_guard: Callable[[Callable[[], None]], None] = lambda fn: fn()
        # Off Windows there's no cheap change detector, so we only rescan while idle
        # rather than interrupting playback every interval.
        self.busy: Callable[[], bool] = lambda: False

    def start(self) -> None:
        self.devices = scan()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        if OS == "windows":
            import comtypes
            comtypes.CoInitialize()
        last = None
        while not self._stop.wait(self.interval):
            try:
                if OS == "windows":
                    ids = _windows_active_ids()
                    if last is None:
                        last = ids
                        continue
                    if ids == last:
                        continue
                    last = ids
                elif self.busy():
                    continue
                self.reinit_guard(self._rescan)
            except Exception:  # never let discovery kill the app
                log.exception("device watcher iteration failed")

    def _rescan(self) -> None:
        refresh_portaudio()
        new = scan()
        if [d.to_dict() for d in new] != [d.to_dict() for d in self.devices]:
            self.devices = new
            self.on_change(new)
