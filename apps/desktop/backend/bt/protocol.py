"""AudioPatch Bluetooth link: framing between the desktop app and the phone app.
Mirrors apps/android/.../link/BtProtocol.kt; spec in docs/bluetooth-link.md.

One RFCOMM connection per phone, carrying a sequence of frames:

    type (u8) | length (u16, little-endian) | payload (length bytes)

    type 1  CONTROL  UTF-8 JSON object (hello / welcome / state / ping / pong / bye)
    type 2  AUDIO    seq (u32 LE) + one Opus packet (20 ms, 48 kHz stereo)
"""
from __future__ import annotations

import json
import struct

VERSION = 1
# RFCOMM service the phone connects to (advertised over SDP by the PC).
SERVICE_UUID = "6b1f1d2e-3c1a-4f7e-9a51-a0d10b0a7c11"

SAMPLE_RATE = 48000
CHANNELS = 2
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000   # 960 per channel
BITRATE = 128_000   # ~16 KB/s: leaves the radio room for headphones on the same PC

CONTROL = 1
AUDIO = 2
HEADER = struct.Struct("<BH")
MAX_PAYLOAD = 4096
PING_INTERVAL_S = 1.0
TIMEOUT_S = 6.0


def frame(kind: int, payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    return HEADER.pack(kind, len(payload)) + payload


def control(msg: dict) -> bytes:
    return frame(CONTROL, json.dumps(msg, separators=(",", ":")).encode())


def audio(seq: int, opus_packet: bytes) -> bytes:
    return frame(AUDIO, struct.pack("<I", seq & 0xFFFFFFFF) + opus_packet)


class FrameReader:
    """Incremental parser: feed() bytes as they arrive, iterate complete frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self._buf += data
        frames = []
        while len(self._buf) >= HEADER.size:
            kind, length = HEADER.unpack_from(self._buf)
            if kind not in (CONTROL, AUDIO) or length > MAX_PAYLOAD:
                raise ValueError("corrupt stream")
            end = HEADER.size + length
            if len(self._buf) < end:
                break
            frames.append((kind, bytes(self._buf[HEADER.size:end])))
            del self._buf[:end]
        return frames


def parse_control(payload: bytes) -> dict:
    msg = json.loads(payload)
    if not isinstance(msg, dict):
        raise ValueError("control message must be an object")
    return msg
