import pytest

from backend.engine.routing import RoutingState


def test_new_source_routes_to_existing_outputs():
    r = RoutingState()
    r.add_output("hp1")
    r.add_output("hp2")
    r.add_source("sys")
    assert r.snapshot()["routes"] == {"sys": ["hp1", "hp2"]}


def test_new_output_joins_existing_sources():
    r = RoutingState()
    r.add_source("sys")
    r.add_source("mic")
    r.add_output("hp1")
    assert r.snapshot()["routes"] == {"sys": ["hp1"], "mic": ["hp1"]}


def test_toggle_route():
    r = RoutingState()
    r.add_source("sys")
    r.add_output("hp1")
    r.set_route("sys", "hp1", False)
    assert r.snapshot()["routes"]["sys"] == []
    r.set_route("sys", "hp1", True)
    assert r.snapshot()["routes"]["sys"] == ["hp1"]


def test_route_requires_added_devices():
    r = RoutingState()
    r.add_source("sys")
    with pytest.raises(KeyError):
        r.set_route("sys", "nope", True)


def test_removing_output_clears_its_routes():
    r = RoutingState()
    r.add_source("sys")
    r.add_output("hp1")
    r.add_output("hp2")
    r.remove_output("hp1")
    snap = r.snapshot()
    assert snap["outputs"] == ["hp2"]
    assert snap["routes"]["sys"] == ["hp2"]


def test_removing_source_drops_its_routes():
    r = RoutingState()
    r.add_source("sys")
    r.add_output("hp1")
    r.remove_source("sys")
    assert r.snapshot()["routes"] == {}


def test_adding_twice_is_idempotent():
    r = RoutingState()
    r.add_output("hp1")
    r.add_source("sys")
    r.set_route("sys", "hp1", False)
    r.add_source("sys")  # must not reset the user's choice
    r.add_output("hp1")
    assert r.snapshot()["routes"]["sys"] == []
    assert r.snapshot()["outputs"] == ["hp1"]


def test_persists_and_restores(tmp_path):
    f = tmp_path / "session.json"
    r = RoutingState(f)
    r.add_output("hp1", "JBL")
    r.add_output("hp2", "CMF")
    r.add_source("sys", "System audio")
    r.set_route("sys", "hp2", False)
    r2 = RoutingState(f)
    assert r2.snapshot() == r.snapshot()
    assert r2.snapshot()["names"]["hp1"] == "JBL"


def test_corrupt_file_starts_empty(tmp_path):
    f = tmp_path / "session.json"
    f.write_text("{not json")
    assert RoutingState(f).snapshot()["sources"] == []


def test_listener_gets_snapshot():
    r = RoutingState()
    seen = []
    r.listeners.append(seen.append)
    r.add_output("hp1")
    assert seen[-1]["outputs"] == ["hp1"]


def test_params_clamped_persisted_and_require_device(tmp_path):
    f = tmp_path / "session.json"
    r = RoutingState(f)
    with pytest.raises(KeyError):
        r.set_params("hp1", volume=0.5)
    r.add_output("hp1")
    r.set_params("hp1", volume=9, muted=True, bogus=1)
    assert r.snapshot()["params"]["hp1"] == {"volume": 2.0, "muted": True}
    assert RoutingState(f).snapshot()["params"]["hp1"]["muted"] is True
