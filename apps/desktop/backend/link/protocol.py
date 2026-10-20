"""AudioPatch Link: the Wi-Fi protocol between the desktop app and phones.
See docs/protocol.md for the full description; keep both in sync.

  discovery  UDP 47800   phone broadcasts {"t":"probe"}, PC replies {"t":"here",...}
  control    TCP 47801   newline-delimited JSON (hello / welcome / state / ping)
  audio      UDP 47802   16-byte header + 16-bit little-endian PCM, 48 kHz stereo
"""
from __future__ import annotations

import json
import struct

import numpy as np

VERSION = 1
DISCOVERY_PORT = 47800
CONTROL_PORT = 47801
AUDIO_PORT = 47802

SAMPLE_RATE = 48000
CHANNELS = 2
PACKET_FRAMES = 240  # 5 ms: 240 frames * 2 ch * 2 bytes = 960 bytes, well under the MTU

MAGIC = b"APL1"
FORMAT_S16LE = 1
# magic(4) stream_id(u32) seq(u32) frames(u16) channels(u8) format(u8)
HEADER = struct.Struct("<4sIIHBB")
MAX_LINE = 4096          # control messages are tiny; refuse anything bigger
PING_INTERVAL_S = 1.0
TIMEOUT_S = 5.0


def encode_msg(msg: dict) -> bytes:
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode()


def pack_audio(stream_id: int, seq: int, block: np.ndarray) -> bytes:
    """block: float32 (n, CHANNELS) in -1..1."""
    pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype("<i2")
    return HEADER.pack(MAGIC, stream_id, seq & 0xFFFFFFFF, len(block), block.shape[1], FORMAT_S16LE) + pcm.tobytes()


def unpack_audio(data: bytes) -> tuple[int, int, np.ndarray] | None:
    """Returns (stream_id, seq, float32 (n, ch)) or None if the packet is malformed."""
    if len(data) < HEADER.size:
        return None
    magic, stream_id, seq, frames, channels, fmt = HEADER.unpack_from(data)
    if magic != MAGIC or fmt != FORMAT_S16LE or channels not in (1, 2):
        return None
    payload = data[HEADER.size:]
    if len(payload) != frames * channels * 2:
        return None
    pcm = np.frombuffer(payload, dtype="<i2").reshape(frames, channels)
    block = pcm.astype(np.float32) / 32768.0
    if channels == 1:
        block = np.repeat(block, 2, axis=1)
    return stream_id, seq, block
