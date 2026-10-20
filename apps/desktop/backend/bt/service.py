"""Bluetooth link to phones.

* Wireless output: the phone app offers an RFCOMM service; this PC connects to
  it and the phone shows up as an output device that receives Opus audio. The
  PC connects (not the phone) because a phone paging a PC from scratch times
  out on common laptop radios, while the PC paging the phone is reliable.
* Phone audio: Windows can act as a Bluetooth speaker for a paired phone
  (AudioPlaybackConnection). The phone's sound then plays into the default
  output (VB-Cable), so it arrives in AudioPatch as part of "System audio".

All Bluetooth I/O runs on one asyncio loop in a background thread. LinkCore
(the protocol) only sees an abstract Transport, so it's tested without radios.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from backend.engine.devices import OS, DeviceInfo
from backend.bt import protocol as P

log = logging.getLogger(__name__)

MAX_SESSIONS = 8
MAX_PENDING_WRITES = 6  # ~120 ms of audio queued; beyond that drop frames, don't grow latency


class Transport(Protocol):
    async def read(self) -> bytes: ...          # b"" on EOF
    async def write(self, data: bytes) -> None: ...
    def close(self) -> None: ...


@dataclass
class Session:
    phone_id: str
    name: str
    model: str
    transport: Transport
    link_id: str | None = None   # Bluetooth device id the PC connected to
    streaming: bool = False
    pending: int = 0
    dropped: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # `pending` is touched by the audio thread and the Bluetooth loop
    counter_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def device_id(self) -> str:
        return f"bt:{self.phone_id}"


class LinkCore:
    """Protocol logic for phone sessions; lives on the Bluetooth asyncio loop."""

    def __init__(self, on_change: Callable[[], None], pc_name: str):
        self.on_change = on_change
        self.pc_name = pc_name
        self.on_session: Callable[[Session], None] = lambda s: None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._sessions: dict[str, Session] = {}

    # -------------------------------------------------- called from any thread
    @property
    def devices(self) -> list[DeviceInfo]:
        return [DeviceInfo(id=s.device_id, name=s.name, direction="output", kind="phone", connected=True,
                           channels=P.CHANNELS, samplerate=P.SAMPLE_RATE,
                           extra={"via": "bluetooth", "model": s.model})
                for s in list(self._sessions.values())]

    def has(self, device_id: str) -> bool:
        return device_id in self._sessions

    def session_for_link(self, link_id: str) -> Session | None:
        return next((s for s in list(self._sessions.values()) if s.link_id == link_id), None)

    def send_audio(self, device_id: str, data: bytes) -> bool:
        """Queue one audio frame; returns False if it was dropped (link backed up)."""
        s = self._sessions.get(device_id)
        if not s or not self.loop:
            return False
        with s.counter_lock:
            if s.pending >= MAX_PENDING_WRITES:
                s.dropped += 1
                return False
            s.pending += 1
        asyncio.run_coroutine_threadsafe(self._write(s, data, audio=True), self.loop)
        return True

    def set_streaming(self, device_id: str, on: bool) -> None:
        s = self._sessions.get(device_id)
        if s and self.loop and s.streaming != on:
            s.streaming = on
            asyncio.run_coroutine_threadsafe(self._write(s, P.control({"t": "state", "streaming": on})), self.loop)

    # ------------------------------------------------------ on the BT loop
    async def _write(self, s: Session, data: bytes, audio: bool = False) -> None:
        try:
            async with s.lock:
                await s.transport.write(data)
        except Exception:
            s.transport.close()  # the reader notices and ends the session
        finally:
            if audio:
                with s.counter_lock:
                    s.pending -= 1

    async def handle(self, transport: Transport, link_id: str | None = None) -> None:
        """Run one phone connection until it closes."""
        self.loop = asyncio.get_running_loop()
        reader = P.FrameReader()
        frames: list[tuple[int, bytes]] = []
        session: Session | None = None

        async def next_frame() -> tuple[int, bytes]:
            while not frames:
                data = await asyncio.wait_for(transport.read(), P.TIMEOUT_S)
                if not data:
                    raise EOFError
                frames.extend(reader.feed(data))
            return frames.pop(0)

        try:
            kind, payload = await next_frame()
            hello = P.parse_control(payload) if kind == P.CONTROL else {}
            session = self._register(hello, transport)
            if not session:
                await transport.write(P.control({"t": "error", "error": "bad hello"}))
                return
            session.link_id = link_id
            self.on_session(session)
            await transport.write(P.control({"t": "welcome", "v": P.VERSION, "name": self.pc_name,
                                             "codec": "opus", "rate": P.SAMPLE_RATE,
                                             "frame_ms": P.FRAME_MS}))
            await transport.write(P.control({"t": "state", "streaming": False}))
            log.info("phone connected: %s", session.name)
            self.on_change()
            while True:
                kind, payload = await next_frame()
                if kind != P.CONTROL:
                    continue  # phones don't send audio on this link
                msg = P.parse_control(payload)
                if msg.get("t") == "ping":
                    await self._write(session, P.control({"t": "pong"}))
                elif msg.get("t") == "bye":
                    return
        except (EOFError, asyncio.TimeoutError, ValueError, OSError, ConnectionError):
            pass
        except Exception:
            log.exception("phone session failed")
        finally:
            transport.close()
            if session and self._sessions.get(session.device_id) is session:
                del self._sessions[session.device_id]
                log.info("phone disconnected: %s", session.name)
                self.on_change()

    def _register(self, hello: dict, transport: Transport) -> Session | None:
        if hello.get("t") != "hello" or hello.get("role") != "output":
            return None
        phone_id = str(hello.get("id", ""))[:64]
        if not phone_id or not phone_id.replace("-", "").isalnum():
            return None
        name = str(hello.get("name", "Phone"))[:64].strip() or "Phone"
        s = Session(phone_id=phone_id, name=name, model=str(hello.get("model", ""))[:64], transport=transport)
        old = self._sessions.get(s.device_id)
        if not old and len(self._sessions) >= MAX_SESSIONS:
            return None
        self._sessions[s.device_id] = s
        if old:  # the same phone reconnected: drop the stale connection
            old.transport.close()
        return s


# --------------------------------------------------------------------- WinRT

class _WinRtTransport:
    def __init__(self, sock):
        from winrt.windows.storage.streams import DataReader, DataWriter, InputStreamOptions
        self._sock = sock
        self._reader = DataReader(sock.input_stream)
        self._reader.input_stream_options = InputStreamOptions.PARTIAL
        self._writer = DataWriter(sock.output_stream)
        self._closed = False

    async def read(self) -> bytes:
        n = await self._reader.load_async(4096)
        if not n:
            return b""
        buf = bytearray(n)
        self._reader.read_bytes(buf)
        return bytes(buf)

    async def write(self, data: bytes) -> None:
        self._writer.write_bytes(data)
        await self._writer.store_async()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._sock.close()
            except Exception:
                pass


class BluetoothService:
    """Owns the Bluetooth loop thread: outgoing RFCOMM links to phones running the
    app (wireless output) and AudioPlaybackConnections for phones sending their
    audio to the PC."""

    RECONNECT_S = 10

    def __init__(self, on_change: Callable[[], None], pc_name: str, memory_file: Path | None = None,
                 wanted: Callable[[], set[str]] = lambda: set()):
        self.core = LinkCore(on_change, pc_name)
        self.core.on_session = self._remember
        self.on_change = on_change
        self.wanted = wanted            # device ids the user has added as outputs
        self.memory_file = memory_file  # phone app id -> Bluetooth device id, for auto-reconnect
        self.running = False
        self.error: str | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._incoming: dict[str, object] = {}  # A2DP receiver connections, by device id
        self._connecting: set[str] = set()
        self._known: dict[str, str] = self._load_memory()
        self._ready = threading.Event()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if OS != "windows":
            self.error = "Phone features need Windows 10 (2004) or later"
            return
        threading.Thread(target=self._thread, name="bluetooth", daemon=True).start()
        self._ready.wait(5)

    def stop(self) -> None:
        if self.loop and self.running:
            try:
                self.call(self._shutdown(), timeout=3)
            except Exception:
                pass

    def call(self, coro: Awaitable, timeout: float = 30):
        """Run a coroutine on the Bluetooth loop from another thread."""
        if not self.loop or not self.running:
            raise RuntimeError(self.error or "Bluetooth isn't running")
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def _thread(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.core.loop = self.loop
        self.loop.create_task(self._init())
        self.loop.run_forever()

    async def _init(self) -> None:
        try:
            from winrt.windows.devices.bluetooth import BluetoothAdapter
            if await BluetoothAdapter.get_default_async() is None:
                raise RuntimeError("no Bluetooth adapter")
            self.running = True
            asyncio.get_running_loop().create_task(self._reconnect_loop())
        except Exception as e:
            self.error = f"Bluetooth unavailable: {e}"
            log.warning(self.error)
        finally:
            self._ready.set()

    async def _shutdown(self) -> None:
        for conn in list(self._incoming.values()):
            try:
                conn.close()
            except Exception:
                pass
        for s in list(self.core._sessions.values()):
            s.transport.close()

    # --------------------------------------------------------------- phones
    async def phones(self) -> list[dict]:
        """Paired phones, with the state of both features for each."""
        from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothMajorClass
        from winrt.windows.devices.enumeration import DeviceInformation
        from winrt.windows.media.audio import AudioPlaybackConnection
        receivers = {}  # Bluetooth address (hex) -> A2DP receiver device id
        for d in await DeviceInformation.find_all_async_aqs_filter(AudioPlaybackConnection.get_device_selector()):
            for part in d.id.upper().replace("&", "_").split("_"):
                if len(part) == 12 and all(c in "0123456789ABCDEF" for c in part):
                    receivers[part] = d.id
        out = []
        sel = BluetoothDevice.get_device_selector_from_pairing_state(True)
        for d in await DeviceInformation.find_all_async_aqs_filter(sel):
            bd = await BluetoothDevice.from_id_async(d.id)
            if not bd or bd.class_of_device.major_class != BluetoothMajorClass.PHONE:
                continue
            addr = f"{bd.bluetooth_address:012X}"
            session = self.core.session_for_link(d.id)
            audio_id = receivers.get(addr)
            out.append({
                "id": d.id, "name": bd.name, "address": addr,
                "output": {"connected": session is not None, "connecting": d.id in self._connecting,
                           "device_id": session.device_id if session else None},
                "audio": {"available": audio_id is not None, "id": audio_id,
                          "connected": bool(audio_id and self._is_open(audio_id))},
            })
        return sorted(out, key=lambda p: p["name"].lower())

    async def connect_output(self, link_id: str) -> None:
        """Connect to the AudioPatch app on a phone (its Wireless output mode)."""
        from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothDevice
        from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceId
        from winrt.windows.networking.sockets import StreamSocket
        if self.core.session_for_link(link_id) or link_id in self._connecting:
            return
        self._connecting.add(link_id)
        self.on_change()
        try:
            bd = await BluetoothDevice.from_id_async(link_id)
            if bd is None:
                raise RuntimeError("that phone isn't paired with this PC")
            hint = (f"couldn't reach the app on {bd.name}: open AudioPatch there, turn on Wireless output, and try "
                    "again. If it keeps failing, turn Bluetooth off and on in Windows (laptop radios can get stuck).")
            sock = None
            for attempt in range(2):  # the phone may still be releasing a previous session
                try:
                    res = await bd.get_rfcomm_services_for_id_with_cache_mode_async(
                        RfcommServiceId.from_uuid(uuid.UUID(P.SERVICE_UUID)), BluetoothCacheMode.UNCACHED)
                    if res.services:
                        svc = res.services[0]
                        sock = StreamSocket()
                        await sock.connect_async(svc.connection_host_name, svc.connection_service_name)
                        break
                except Exception as e:  # WinRT raises bare OSErrors with empty messages
                    log.info("connect to %s failed (attempt %d): %r", bd.name, attempt + 1, e)
                    sock = None
                await asyncio.sleep(2)
            if sock is None:
                raise RuntimeError(hint)
            asyncio.get_running_loop().create_task(self.core.handle(_WinRtTransport(sock), link_id))
            for _ in range(60):  # wait for the phone's hello so it's listed when we return
                if self.core.session_for_link(link_id):
                    break
                await asyncio.sleep(0.05)
        finally:
            self._connecting.discard(link_id)
            self.on_change()

    async def disconnect_output(self, link_id: str) -> None:
        s = self.core.session_for_link(link_id)
        if s:
            self._known.pop(s.phone_id, None)  # the user chose to disconnect: don't auto-reconnect
            self._save_memory()
            s.transport.close()

    async def _reconnect_loop(self) -> None:
        """Reconnect phones that are part of the setup but dropped (out of range,
        app restarted). Quietly: the phone may simply not be around."""
        while True:
            await asyncio.sleep(self.RECONNECT_S)
            wanted = self.wanted()
            for phone_id, link_id in list(self._known.items()):
                if f"bt:{phone_id}" in wanted and not self.core.has(f"bt:{phone_id}"):
                    try:
                        await asyncio.wait_for(self.connect_output(link_id), 20)
                    except Exception:
                        pass

    def _remember(self, s: Session) -> None:
        if s.link_id and self._known.get(s.phone_id) != s.link_id:
            self._known[s.phone_id] = s.link_id
            self._save_memory()

    def _load_memory(self) -> dict[str, str]:
        try:
            return dict(json.loads(self.memory_file.read_text("utf-8"))) if self.memory_file else {}
        except Exception:
            return {}

    def _save_memory(self) -> None:
        if not self.memory_file:
            return
        try:
            self.memory_file.parent.mkdir(parents=True, exist_ok=True)
            self.memory_file.write_text(json.dumps(self._known, indent=2), "utf-8")
        except Exception:
            log.exception("couldn't save phone list")

    # ------------------------------------------- phone -> PC (A2DP receiver)
    async def connect_audio(self, device_id: str) -> str:
        from winrt.windows.media.audio import AudioPlaybackConnection, AudioPlaybackConnectionOpenResultStatus
        if self._is_open(device_id):
            return "already connected"
        conn = AudioPlaybackConnection.try_create_from_id(device_id)
        if conn is None:
            raise RuntimeError("this phone can't send audio to the PC")
        conn.add_state_changed(lambda c, _a: self.on_change())
        await conn.start_async()
        result = await conn.open_async()
        if result.status != AudioPlaybackConnectionOpenResultStatus.SUCCESS:
            conn.close()
            hint = {
                AudioPlaybackConnectionOpenResultStatus.REQUEST_TIMED_OUT:
                    "the phone didn't answer; is its Bluetooth on and is it nearby?",
                AudioPlaybackConnectionOpenResultStatus.DENIED_BY_SYSTEM: "Windows refused the connection",
            }.get(result.status, f"failed ({result.status.name})")
            raise RuntimeError(hint)
        self._incoming[device_id] = conn
        self.on_change()
        return "connected"

    async def disconnect_audio(self, device_id: str) -> None:
        conn = self._incoming.pop(device_id, None)
        if conn:
            conn.close()
        self.on_change()

    def _is_open(self, device_id: str) -> bool:
        from winrt.windows.media.audio import AudioPlaybackConnectionState
        conn = self._incoming.get(device_id)
        return bool(conn and conn.state == AudioPlaybackConnectionState.OPENED)
