"""The phone link, exercised with a simulated phone over real localhost sockets."""
import json
import socket
import threading
import time

import numpy as np
import pytest

from backend.engine.mixer import CH, OutputMixer, Params, Route, SourceNode
from backend.link import protocol as P
from backend.link.service import LinkService
from backend.link.streams import NetworkOutputStream, NetworkSourceStream


@pytest.fixture
def link():
    changes = []
    svc = LinkService(on_change=lambda: changes.append(1), host="127.0.0.1",
                      discovery_port=0, control_port=0, audio_port=0, name="TestPC")
    svc.start()
    svc.changes = changes
    assert svc.running, svc.error
    yield svc
    svc.stop()


class FakePhone:
    def __init__(self, link, role, phone_id="phone-1", name="Test Phone"):
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.udp.settimeout(2)
        self.tcp = socket.create_connection(("127.0.0.1", link.ports["control"]), timeout=2)
        self.reader = self.tcp.makefile("rb")
        hello = {"t": "hello", "v": 1, "role": role, "id": phone_id, "name": name, "model": "Sim"}
        if role == "output":
            hello["udp_port"] = self.udp.getsockname()[1]
        self.tcp.sendall(P.encode_msg(hello))

    def recv(self):
        return json.loads(self.reader.readline())

    def close(self):
        self.reader.close()  # makefile() holds a socket reference of its own
        self.tcp.close()
        self.udp.close()


def wait_for(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_packet_roundtrip_and_validation():
    block = np.linspace(-1, 1, P.PACKET_FRAMES * CH, dtype=np.float32).reshape(-1, CH)
    pkt = P.pack_audio(7, 42, block)
    assert len(pkt) == P.HEADER.size + P.PACKET_FRAMES * CH * 2
    sid, seq, out = P.unpack_audio(pkt)
    assert (sid, seq) == (7, 42)
    np.testing.assert_allclose(out, block, atol=1 / 16000)
    assert P.unpack_audio(b"junk") is None
    assert P.unpack_audio(pkt[:-2]) is None            # truncated payload
    assert P.unpack_audio(b"XXXX" + pkt[4:]) is None   # wrong magic


def test_discovery_reply(link):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.sendto(json.dumps({"t": "probe", "v": 1}).encode(), ("127.0.0.1", link.ports["discovery"]))
    reply = json.loads(s.recvfrom(2048)[0])
    assert reply["t"] == "here" and reply["name"] == "TestPC"
    assert reply["control_port"] == link.ports["control"]
    s.sendto(b"\xff not json", ("127.0.0.1", link.ports["discovery"]))  # must not kill the loop
    s.sendto(json.dumps({"t": "probe"}).encode(), ("127.0.0.1", link.ports["discovery"]))
    assert json.loads(s.recvfrom(2048)[0])["t"] == "here"


def test_output_phone_appears_and_disappears(link):
    phone = FakePhone(link, "output")
    welcome = phone.recv()
    assert welcome["t"] == "welcome" and welcome["stream_id"] > 0
    assert phone.recv() == {"t": "state", "streaming": False}
    assert wait_for(lambda: len(link.devices) == 1)
    d = link.devices[0]
    assert (d.id, d.direction, d.kind, d.name) == ("net:phone-1:output", "output", "network", "Test Phone")
    phone.tcp.sendall(P.encode_msg({"t": "ping"}))
    assert phone.recv() == {"t": "pong"}
    phone.close()
    assert wait_for(lambda: link.devices == [])


def test_streams_paced_audio_to_output_phone(link):
    phone = FakePhone(link, "output")
    welcome = phone.recv()
    phone.recv()  # state: not streaming
    assert wait_for(lambda: link.devices)
    mixer = OutputMixer("o", Params(), 20)
    tone = np.full((48000, CH), 0.25, np.float32)
    mixer.play_tone(tone)
    stream = NetworkOutputStream(link.devices[0], mixer, link)
    stream.start()
    assert phone.recv() == {"t": "state", "streaming": True}
    got, t0 = [], time.perf_counter()
    while len(got) < 100:  # 0.5 s of audio
        got.append(P.unpack_audio(phone.udp.recvfrom(4096)[0]))
    elapsed = time.perf_counter() - t0
    stream.close()
    assert phone.recv() == {"t": "state", "streaming": False}
    assert all(sid == welcome["stream_id"] for sid, _, _ in got)
    seqs = [seq for _, seq, _ in got]
    assert seqs == list(range(seqs[0], seqs[0] + 100))  # in order, none skipped
    assert 0.40 < elapsed < 0.65  # paced at real time (100 x 5 ms), not burst out
    audio = np.concatenate([b for _, _, b in got])
    assert np.allclose(audio, 0.25, atol=1e-3)
    phone.close()


def test_source_phone_feeds_the_mixer(link):
    phone = FakePhone(link, "source", phone_id="phone-2")
    welcome = phone.recv()
    assert welcome["audio_port"] == link.ports["audio"]
    assert wait_for(lambda: link.devices)
    dev = link.devices[0]
    assert (dev.direction, dev.id) == ("input", "net:phone-2:source")
    node = SourceNode(dev.id, Params())
    route = Route(dev.id, "out")
    node.rings = (route.ring,)
    stream = NetworkSourceStream(dev, node, link)
    stream.start()
    block = np.full((P.PACKET_FRAMES, CH), -0.5, np.float32)
    for seq in range(20):
        phone.udp.sendto(P.pack_audio(welcome["stream_id"], seq, block), ("127.0.0.1", link.ports["audio"]))
    phone.udp.sendto(P.pack_audio(12345, 0, block), ("127.0.0.1", link.ports["audio"]))  # unknown stream: ignored
    assert wait_for(lambda: route.ring.available == 20 * P.PACKET_FRAMES)
    out = np.zeros((route.ring.available, CH), np.float32)
    route.ring.read_into(out)
    assert np.allclose(out, -0.5, atol=1e-3)
    stream.close()
    phone.close()


def test_rejects_bad_hellos(link):
    for hello in ({"t": "hello", "role": "spy", "id": "x"},
                  {"t": "hello", "role": "output", "id": "x"},                 # no udp port
                  {"t": "hello", "role": "source", "id": "bad id; drop table"},
                  {"t": "nope"}):
        c = socket.create_connection(("127.0.0.1", link.ports["control"]), timeout=2)
        c.sendall(P.encode_msg(hello))
        assert json.loads(c.makefile("rb").readline())["t"] == "error"
        c.close()
    c = socket.create_connection(("127.0.0.1", link.ports["control"]), timeout=2)
    c.sendall(b"x" * (P.MAX_LINE + 10) + b"\n")  # oversized: connection dropped
    assert c.makefile("rb").readline() == b""
    c.close()
    assert link.devices == []


def test_same_phone_reconnecting_replaces_old_session(link):
    a = FakePhone(link, "output")
    a.recv()
    b = FakePhone(link, "output")  # same phone id
    b.recv()
    assert wait_for(lambda: len(link.devices) == 1)
    a.close()
    time.sleep(0.2)
    assert len(link.devices) == 1  # closing the stale one doesn't remove the new one
    b.close()
    assert wait_for(lambda: link.devices == [])
