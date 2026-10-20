"""Bluetooth link: framing, Opus, and the session protocol over a fake transport."""
import asyncio
import ctypes
import json
import threading
import time

import numpy as np
import pytest

from backend.bt import protocol as P
from backend.bt.opus import OpusEncoder, _load
from backend.bt.service import MAX_PENDING_WRITES, LinkCore
from backend.bt.streams import BtOutputStream
from backend.engine.mixer import CH, OutputMixer, Params


def test_frame_reader_handles_partial_and_batched_input():
    data = P.control({"t": "ping"}) + P.audio(7, b"\x01\x02\x03") + P.control({"t": "bye"})
    r = P.FrameReader()
    got = []
    for i in range(0, len(data), 3):  # dribble in 3 bytes at a time
        got += r.feed(data[i:i + 3])
    assert [k for k, _ in got] == [P.CONTROL, P.AUDIO, P.CONTROL]
    assert json.loads(got[0][1]) == {"t": "ping"}
    assert got[1][1] == (7).to_bytes(4, "little") + b"\x01\x02\x03"
    with pytest.raises(ValueError):
        P.FrameReader().feed(b"\x09\x01\x00x")  # unknown frame type


def test_opus_roundtrip_preserves_the_signal():
    enc = OpusEncoder(48000, 2, P.BITRATE)
    lib = _load()
    lib.opus_decoder_create.restype = ctypes.c_void_p
    lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    err = ctypes.c_int()
    dec = lib.opus_decoder_create(48000, 2, ctypes.byref(err))
    t = np.arange(48000 * 2) / 48000
    sig = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo = np.stack([sig, sig], axis=1)
    out, sizes = [], []
    pcm = np.zeros(960 * 2, np.int16)
    for i in range(0, len(stereo), 960):
        pkt = enc.encode(stereo[i:i + 960])
        sizes.append(len(pkt))
        n = lib.opus_decode(dec, pkt, len(pkt), pcm.ctypes.data, 960, 0)
        assert n == 960
        out.append(pcm.reshape(-1, 2)[:, 0].astype(np.float32) / 32768)
    y = np.concatenate(out)[48000:]                # skip codec warm-up
    x = sig[48000:]
    # Opus delays its output by its lookahead; find that delay, then compare.
    lag = min(range(1, 1200), key=lambda d: np.mean((y[d:d + 4800] - x[:4800]) ** 2))
    err_db = 10 * np.log10(np.mean((y[lag:] - x[:-lag]) ** 2) / np.mean(x ** 2))
    assert err_db < -20, err_db                   # faithful copy of the tone
    assert np.mean(sizes) * 50 * 8 < P.BITRATE * 1.15  # stays near the target bitrate


class FakeTransport:
    """In-memory transport: `incoming` is what the phone sends, `sent` what the PC wrote."""

    def __init__(self, loop):
        self.loop = loop
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.write_delay = 0.0

    async def read(self) -> bytes:
        if self.closed:
            return b""
        return await self.incoming.get()

    async def write(self, data: bytes) -> None:
        if self.write_delay:
            await asyncio.sleep(self.write_delay)
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True
        self.incoming.put_nowait(b"")

    def phone_sends(self, msg: dict) -> None:
        self.loop.call_soon_threadsafe(self.incoming.put_nowait, P.control(msg))

    def frames(self):
        r = P.FrameReader()
        out = []
        for chunk in list(self.sent):
            out += r.feed(chunk)
        return out

    def controls(self):
        return [json.loads(p) for k, p in self.frames() if k == P.CONTROL]


@pytest.fixture
def bt():
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    changes = []
    core = LinkCore(on_change=lambda: changes.append(1), pc_name="TestPC")
    core.loop = loop
    core.changes = changes

    def connect(hello):
        t = FakeTransport(loop)
        asyncio.run_coroutine_threadsafe(core.handle(t), loop)
        t.phone_sends(hello)
        return t

    core.connect = connect
    yield core
    loop.call_soon_threadsafe(loop.stop)


HELLO = {"t": "hello", "v": 1, "role": "output", "id": "phone-1", "name": "Test Phone", "model": "Sim"}


def wait_for(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_phone_appears_gets_welcome_and_leaves(bt):
    t = bt.connect(HELLO)
    assert wait_for(lambda: len(t.controls()) >= 2)
    welcome, state = t.controls()[:2]
    assert welcome["t"] == "welcome" and welcome["codec"] == "opus" and welcome["name"] == "TestPC"
    assert state == {"t": "state", "streaming": False}
    d = bt.devices[0]
    assert (d.id, d.kind, d.direction, d.name) == ("bt:phone-1", "phone", "output", "Test Phone")
    t.phone_sends({"t": "ping"})
    assert wait_for(lambda: {"t": "pong"} in t.controls())
    t.phone_sends({"t": "bye"})
    assert wait_for(lambda: bt.devices == [])


def test_bad_hellos_are_rejected(bt):
    for hello in ({"t": "hello", "role": "source", "id": "x"},
                  {"t": "hello", "role": "output", "id": "bad id; drop"},
                  {"t": "nope"}):
        t = bt.connect(hello)
        assert wait_for(lambda: t.closed)
        assert t.controls()[0]["t"] == "error"
    assert bt.devices == []


def test_streaming_state_and_audio_frames(bt):
    t = bt.connect(HELLO)
    assert wait_for(lambda: bt.devices)
    mixer = OutputMixer("o", Params(), 20)
    mixer.play_tone(np.full((48000, CH), 0.3, np.float32))
    stream = BtOutputStream(bt.devices[0], mixer, bt)
    t0 = time.perf_counter()
    stream.start()
    assert wait_for(lambda: sum(1 for k, _ in t.frames() if k == P.AUDIO) >= 25)
    elapsed = time.perf_counter() - t0
    stream.close()
    assert wait_for(lambda: t.controls()[-1] == {"t": "state", "streaming": False})
    assert {"t": "state", "streaming": True} in t.controls()
    audio = [p for k, p in t.frames() if k == P.AUDIO]
    seqs = [int.from_bytes(p[:4], "little") for p in audio]
    assert seqs == list(range(len(seqs)))       # in order, none dropped
    assert 0.40 < elapsed < 0.75                # paced at 50 frames/s, not burst out
    assert all(50 < len(p) - 4 < 1000 for p in audio)  # real Opus packets


def test_backed_up_link_drops_frames_instead_of_growing_latency(bt):
    t = bt.connect(HELLO)
    assert wait_for(lambda: bt.devices)
    t.write_delay = 0.5  # radio stalls
    results = [bt.send_audio("bt:phone-1", P.audio(i, b"x" * 100)) for i in range(20)]
    assert results.count(True) == MAX_PENDING_WRITES
    assert not all(results)


def test_same_phone_reconnecting_replaces_old_session(bt):
    a = bt.connect(HELLO)
    assert wait_for(lambda: bt.devices)
    b = bt.connect(HELLO)
    assert wait_for(lambda: a.closed)
    time.sleep(0.1)
    assert len(bt.devices) == 1  # the stale session closing didn't remove the new one
    b.phone_sends({"t": "bye"})
    assert wait_for(lambda: bt.devices == [])
