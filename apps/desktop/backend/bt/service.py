"""Bluetooth link to phones.

* Wireless output: the phone app connects to this PC's RFCOMM service and shows
  up as an output device; the PC streams Opus audio to it.
* Phone audio: Windows can act as a Bluetooth speaker for a paired phone
  (AudioPlaybackConnection). The phone's sound then plays into the default
  output (VB-Cable), so it arrives in AudioPatch as part of "System audio".

All Bluetooth I/O runs on one asyncio loop in a background thread. LinkCore
(the protocol) only sees an abstract Transport, so it's tested without radios.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
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

    async def handle(self, transport: Transport) -> None:
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
            await transport.write(P.control({"t": "welcome", "v": P.VERSION, "name": self.pc_name,
                                             "codec": "opus", "rate": P.SAMPLE_RATE,
                                             "frame_ms": P.FRAME_MS}))
            await transport.write(P.control({"t": "state", "streaming": False}))
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
    """Owns the Bluetooth loop thread: the RFCOMM service for phone outputs and
    AudioPlaybackConnections for phones sending audio to the PC."""

    def __init__(self, on_change: Callable[[], None], pc_name: str):
        self.core = LinkCore(on_change, pc_name)
        self.on_change = on_change
        self.running = False
        self.error: str | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._provider = None
        self._listener = None
        self._incoming: dict[str, object] = {}  # phone device id -> AudioPlaybackConnection
        self._ready = threading.Event()

    def start(self) -> None:
        if OS != "windows":
            self.error = "Bluetooth link needs Windows 10 (2004) or later"
            return
        threading.Thread(target=self._thread, name="bluetooth", daemon=True).start()
        self._ready.wait(5)

    def stop(self) -> None:
        if self.loop:
            self.call(self._shutdown(), timeout=3)

    def call(self, coro: Awaitable, timeout: float = 10):
        """Run a coroutine on the Bluetooth loop from another thread."""
        if not self.loop:
            raise RuntimeError(self.error or "Bluetooth isn't running")
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def _thread(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._serve())
        self.loop.run_forever()

    async def _serve(self) -> None:
        try:
            from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceId, RfcommServiceProvider
            from winrt.windows.networking.sockets import StreamSocketListener
            self._provider = await RfcommServiceProvider.create_async(
                RfcommServiceId.from_uuid(uuid.UUID(P.SERVICE_UUID)))
            self._listener = StreamSocketListener()
            loop = asyncio.get_running_loop()
            self.core.loop = loop
            self._listener.add_connection_received(
                lambda _l, args: loop.call_soon_threadsafe(
                    lambda: loop.create_task(self.core.handle(_WinRtTransport(args.socket)))))
            await self._listener.bind_service_name_async(self._provider.service_id.as_string())
            self._provider.start_advertising(self._listener)
            self.running = True
            log.info("Bluetooth link advertising %s", P.SERVICE_UUID)
        except Exception as e:  # no adapter, Bluetooth off, ...
            self.error = f"Bluetooth unavailable: {e}"
            log.warning(self.error)
        finally:
            self._ready.set()

    async def _shutdown(self) -> None:
        try:
            if self._provider:
                self._provider.stop_advertising()
            for conn in self._incoming.values():
                conn.close()
        except Exception:
            pass

    # ------------------------------------------- phone -> PC (A2DP receiver)
    async def phones(self) -> list[dict]:
        """Paired devices that can play their audio on this PC."""
        from winrt.windows.devices.enumeration import DeviceInformation
        from winrt.windows.media.audio import AudioPlaybackConnection
        found = await DeviceInformation.find_all_async_aqs_filter(AudioPlaybackConnection.get_device_selector())
        return [{"id": d.id, "name": d.name, "connected": self._is_open(d.id)} for d in found]

    async def connect_phone(self, device_id: str) -> str:
        from winrt.windows.media.audio import AudioPlaybackConnection, AudioPlaybackConnectionOpenResultStatus
        if self._is_open(device_id):
            return "already connected"
        conn = AudioPlaybackConnection.try_create_from_id(device_id)
        if conn is None:
            raise RuntimeError("this device can't send audio to the PC")
        conn.add_state_changed(lambda c, _a: self.on_change())
        await conn.start_async()
        result = await conn.open_async()
        if result.status != AudioPlaybackConnectionOpenResultStatus.SUCCESS:
            conn.close()
            hint = {
                AudioPlaybackConnectionOpenResultStatus.REQUEST_TIMED_OUT: "the phone didn't answer; is Bluetooth on and the phone nearby?",
                AudioPlaybackConnectionOpenResultStatus.DENIED_BY_SYSTEM: "Windows refused the connection",
            }.get(result.status, f"failed ({result.status.name})")
            raise RuntimeError(hint)
        self._incoming[device_id] = conn
        self.on_change()
        return "connected"

    async def disconnect_phone(self, device_id: str) -> None:
        conn = self._incoming.pop(device_id, None)
        if conn:
            conn.close()
        self.on_change()

    def _is_open(self, device_id: str) -> bool:
        from winrt.windows.media.audio import AudioPlaybackConnectionState
        conn = self._incoming.get(device_id)
        return bool(conn and conn.state == AudioPlaybackConnectionState.OPENED)
