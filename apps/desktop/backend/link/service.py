"""LinkService: lets phones on the same Wi-Fi appear as AudioPatch devices.

A phone in "output" mode shows up as an output (the PC streams audio to it);
a phone in "source" mode shows up as an input (it streams audio to the PC).
Phones only *appear*; no audio flows until the user adds them in the
dashboard, exactly like any other device.
"""
from __future__ import annotations

import json
import logging
import random
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from backend.engine.devices import DeviceInfo
from backend.link import protocol as P

log = logging.getLogger(__name__)

MAX_SESSIONS = 16


@dataclass
class Session:
    phone_id: str
    name: str
    role: str                 # "output" (PC -> phone) or "source" (phone -> PC)
    ip: str
    udp_port: int | None      # where the phone listens for audio (output role)
    stream_id: int
    conn: socket.socket
    model: str = ""
    streaming: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def device_id(self) -> str:
        return f"net:{self.phone_id}:{self.role}"

    def send(self, msg: dict) -> None:
        try:
            with self.lock:
                self.conn.sendall(P.encode_msg(msg))
        except OSError:
            pass  # the reader thread notices the dead socket and cleans up


class LinkService:
    def __init__(self, on_change: Callable[[], None], host: str = "0.0.0.0",
                 discovery_port: int = P.DISCOVERY_PORT, control_port: int = P.CONTROL_PORT,
                 audio_port: int = P.AUDIO_PORT, name: str | None = None):
        self.on_change = on_change
        self.host = host
        self.ports = {"discovery": discovery_port, "control": control_port, "audio": audio_port}
        self.name = name or socket.gethostname()
        self.pc_id = uuid.uuid4().hex[:12]
        self.running = False
        self.error: str | None = None
        self._sessions: dict[str, Session] = {}  # device_id -> session
        self._sinks: dict[int, Callable[[np.ndarray], None]] = {}  # stream_id -> callback
        self._lock = threading.Lock()
        self._socks: list[socket.socket] = []
        self._audio_sock: socket.socket | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        try:
            disc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            disc.bind((self.host, self.ports["discovery"]))
            ctrl = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ctrl.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            ctrl.bind((self.host, self.ports["control"]))
            ctrl.listen(8)
            audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            audio.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            audio.bind((self.host, self.ports["audio"]))
        except OSError as e:
            self.error = f"Phone link unavailable: {e}"
            log.warning(self.error)
            return
        # Ports may be 0 in tests: record what the OS picked.
        self.ports = {"discovery": disc.getsockname()[1], "control": ctrl.getsockname()[1],
                      "audio": audio.getsockname()[1]}
        self._socks = [disc, ctrl, audio]
        self._audio_sock = audio
        self.running = True
        for target, sock in ((self._discovery_loop, disc), (self._accept_loop, ctrl), (self._audio_loop, audio)):
            threading.Thread(target=target, args=(sock,), name=f"link-{target.__name__}", daemon=True).start()
        log.info("phone link listening on %s", self.ports)

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        for s in self._socks:
            try:
                s.close()
            except OSError:
                pass
        for sess in list(self._sessions.values()):
            try:
                sess.conn.close()
            except OSError:
                pass

    # -------------------------------------------------------------- devices
    @property
    def devices(self) -> list[DeviceInfo]:
        with self._lock:
            sessions = list(self._sessions.values())
        return [DeviceInfo(
            id=s.device_id, name=s.name, direction="output" if s.role == "output" else "input",
            kind="network", connected=True, channels=P.CHANNELS, samplerate=P.SAMPLE_RATE,
            extra={"addr": s.ip, "model": s.model, "via": "wifi"},
        ) for s in sessions]

    def session(self, device_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(device_id)

    def set_streaming(self, device_id: str, on: bool) -> None:
        """Tell an output phone whether the PC is now sending it audio."""
        s = self.session(device_id)
        if s and s.streaming != on:
            s.streaming = on
            s.send({"t": "state", "streaming": on})

    # ---------------------------------------------------------------- audio
    def send_audio(self, device_id: str, packet: bytes) -> None:
        s = self.session(device_id)
        if s and s.udp_port and self._audio_sock:
            try:
                self._audio_sock.sendto(packet, (s.ip, s.udp_port))
            except OSError:
                pass

    def register_sink(self, stream_id: int, callback: Callable[[np.ndarray], None]) -> None:
        self._sinks[stream_id] = callback

    def unregister_sink(self, stream_id: int) -> None:
        self._sinks.pop(stream_id, None)

    # -------------------------------------------------------------- threads
    def _discovery_loop(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(2048)
                msg = json.loads(data[:P.MAX_LINE])
                if isinstance(msg, dict) and msg.get("t") == "probe":
                    reply = {"t": "here", "v": P.VERSION, "name": self.name, "id": self.pc_id,
                             "control_port": self.ports["control"]}
                    sock.sendto(json.dumps(reply).encode(), addr)
            except OSError:
                if self._stop.is_set():
                    return
            except (ValueError, UnicodeDecodeError):
                continue  # not one of ours

    def _accept_loop(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session_loop, args=(conn, addr[0]), daemon=True).start()

    def _audio_loop(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data, _addr = sock.recvfrom(65536)
            except OSError:
                if self._stop.is_set():
                    return
                continue
            parsed = P.unpack_audio(data)
            if parsed:
                sink = self._sinks.get(parsed[0])
                if sink:
                    try:
                        sink(parsed[2])
                    except Exception:
                        log.exception("network source sink failed")

    def _session_loop(self, conn: socket.socket, ip: str) -> None:
        conn.settimeout(P.TIMEOUT_S)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        reader = conn.makefile("rb")
        session: Session | None = None
        try:
            hello = _read_msg(reader)
            session = self._register(hello, conn, ip)
            if not session:
                conn.sendall(P.encode_msg({"t": "error", "error": "bad hello"}))
                return
            session.send({"t": "welcome", "v": P.VERSION, "name": self.name,
                          "stream_id": session.stream_id, "audio_port": self.ports["audio"]})
            session.send({"t": "state", "streaming": False})
            self.on_change()
            while not self._stop.is_set():
                msg = _read_msg(reader)
                if msg.get("t") == "ping":
                    session.send({"t": "pong"})
                elif msg.get("t") == "bye":
                    return
        except (OSError, ValueError, EOFError):
            pass
        finally:
            # Close the file wrapper too: it holds its own reference to the
            # socket, so conn.close() alone would leave the connection open.
            for closable in (reader, conn):
                try:
                    closable.close()
                except OSError:
                    pass
            if session:
                with self._lock:
                    if self._sessions.get(session.device_id) is session:
                        del self._sessions[session.device_id]
                self._sinks.pop(session.stream_id, None)
                self.on_change()

    def _register(self, hello: dict, conn: socket.socket, ip: str) -> Session | None:
        if hello.get("t") != "hello" or hello.get("role") not in ("output", "source"):
            return None
        phone_id = str(hello.get("id", ""))[:64]
        name = str(hello.get("name", "Phone"))[:64].strip() or "Phone"
        if not phone_id.replace("-", "").isalnum():
            return None
        udp_port = hello.get("udp_port")
        if hello["role"] == "output" and not (isinstance(udp_port, int) and 0 < udp_port < 65536):
            return None
        sess = Session(phone_id=phone_id, name=name, role=hello["role"], ip=ip,
                       udp_port=udp_port if hello["role"] == "output" else None,
                       stream_id=random.getrandbits(31) | 1, conn=conn,
                       model=str(hello.get("model", ""))[:64])
        with self._lock:
            old = self._sessions.get(sess.device_id)
            if not old and len(self._sessions) >= MAX_SESSIONS:
                return None
            self._sessions[sess.device_id] = sess
        if old:  # same phone reconnected: drop the stale connection
            try:
                old.conn.close()
            except OSError:
                pass
        return sess


def _read_msg(reader) -> dict:
    line = reader.readline(P.MAX_LINE + 1)
    if not line:
        raise EOFError
    if len(line) > P.MAX_LINE:
        raise ValueError("control message too long")
    msg = json.loads(line)
    if not isinstance(msg, dict):
        raise ValueError("expected an object")
    return msg


def lan_addresses() -> list[str]:
    """IPv4 addresses phones can reach this PC on (for manual connection)."""
    found = []
    try:  # the interface used for the default route (no packet is sent)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found and not ip.startswith("127.") and not ip.startswith("169.254."):
                found.append(ip)
    except OSError:
        pass
    return found
