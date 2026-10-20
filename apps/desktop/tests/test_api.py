"""API-level checks with a fake device list (no audio hardware, no lifespan)."""
import os
import tempfile

os.environ["AUDIOPATCH_DATA"] = tempfile.mkdtemp()  # must be set before importing the app

import pytest
from fastapi.testclient import TestClient

from backend.api import app as api
from backend.engine.devices import DeviceInfo

OUT = DeviceInfo(id="out1", name="Headphones (Test)", direction="output", kind="bluetooth", connected=True, channels=2)
SRC = DeviceInfo(id="loopback:system", name="System audio", direction="input", kind="loopback", connected=True, channels=2)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api.watcher, "devices", [OUT, SRC])
    monkeypatch.setattr(api.engine, "request_reconcile", lambda: None)  # don't open real streams
    for sid in list(api.routing.sources):
        api.routing.remove_source(sid)
    for oid in list(api.routing.outputs):
        api.routing.remove_output(oid)
    return TestClient(api.app)  # no `with`: skips lifespan (device watcher, engine)


def test_every_param_from_the_ui_is_stored(client):
    assert client.post("/api/outputs", json={"id": "out1"}).status_code == 200
    r = client.post("/api/params", json={"id": "out1", "volume": 0.5, "muted": True, "delay_ms": 120})
    assert r.status_code == 200
    assert r.json()["params"]["out1"] == {"volume": 0.5, "muted": True, "delay_ms": 120.0}


def test_delay_is_clamped(client):
    client.post("/api/outputs", json={"id": "out1"})
    r = client.post("/api/params", json={"id": "out1", "delay_ms": 9999})
    assert r.json()["params"]["out1"]["delay_ms"] == 1000.0


def test_routing_flow(client):
    client.post("/api/outputs", json={"id": "out1"})
    r = client.post("/api/sources", json={"id": "loopback:system"})
    assert r.json()["routes"] == {"loopback:system": ["out1"]}
    r = client.post("/api/routes", json={"source": "loopback:system", "output": "out1", "on": False})
    assert r.json()["routes"]["loopback:system"] == []


def test_unknown_or_disconnected_devices_are_rejected(client, monkeypatch):
    assert client.post("/api/outputs", json={"id": "nope"}).status_code == 404
    gone = DeviceInfo(id="out2", name="Gone", direction="output", kind="bluetooth", connected=False)
    monkeypatch.setattr(api.watcher, "devices", [OUT, SRC, gone])
    assert client.post("/api/outputs", json={"id": "out2"}).status_code == 409


def test_params_for_unadded_device_is_400(client):
    assert client.post("/api/params", json={"id": "out1", "volume": 1}).status_code == 400


def test_dashboard_page_is_always_revalidated(client):
    """After an update, a cached index.html would load the old dashboard."""
    if not api.DIST.exists():
        pytest.skip("dashboard not built")
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()
    assert r.headers.get("cache-control") == "no-cache"
