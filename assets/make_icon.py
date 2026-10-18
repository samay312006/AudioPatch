"""Generates assets/icon.ico (16-256 px) for the packaged app. Run once; no deps beyond numpy."""
import struct
import zlib
from pathlib import Path

import numpy as np


def render(size: int) -> np.ndarray:
    ss = 4  # supersample for smooth edges
    n = size * ss
    y, x = np.mgrid[0:n, 0:n] / n
    # rounded-square mask
    r = 0.22
    dx = np.maximum(np.abs(x - 0.5) - (0.5 - r), 0)
    dy = np.maximum(np.abs(y - 0.5) - (0.5 - r), 0)
    tile = (dx ** 2 + dy ** 2) <= r ** 2
    # diagonal gradient: sky (#38bdf8) -> violet (#8b5cf6)
    t = ((x + y) / 2)[..., None]
    rgb = (1 - t) * np.array([56, 189, 248]) + t * np.array([139, 92, 246])
    # five equaliser bars
    bars = np.zeros((n, n), bool)
    heights = [0.30, 0.52, 0.66, 0.44, 0.24]
    w, gap = 0.09, 0.045
    left = 0.5 - (5 * w + 4 * gap) / 2
    for i, h in enumerate(heights):
        x0 = left + i * (w + gap)
        bars |= (x >= x0) & (x <= x0 + w) & (np.abs(y - 0.5) <= h / 2)
    img = np.zeros((n, n, 4))
    img[..., :3] = np.where(bars[..., None], 255, rgb)
    img[..., 3] = tile * 255
    # downsample
    img = img.reshape(size, ss, size, ss, 4).mean(axis=(1, 3))
    return img.round().astype(np.uint8)


def png(img: np.ndarray) -> bytes:
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[row].tobytes() for row in range(h))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    images = [png(render(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + entries + b"".join(images)


if __name__ == "__main__":
    out = Path(__file__).with_name("icon.ico")
    out.write_bytes(ico())
    Path(__file__).with_name("icon.png").write_bytes(png(render(256)))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
