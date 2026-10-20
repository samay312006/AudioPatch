import numpy as np
import pytest

from backend.engine.calibration import find_click, make_click, suggest_delays
from backend.engine.mixer import SR


def fake_recording(offset, gain=0.05, noise=0.01, seconds=1.0, echo=True, seed=0):
    """A noisy room recording with an attenuated, slightly filtered click at `offset`."""
    rng = np.random.default_rng(seed)
    rec = rng.normal(0, noise, int(SR * seconds)).astype(np.float32)
    click = make_click()[:, 0] * gain
    # crude speaker/mic coloration: a 3-tap smoothing filter
    click = np.convolve(click, [0.25, 0.5, 0.25], mode="same").astype(np.float32)
    rec[offset:offset + len(click)] += click
    if echo:  # a weaker reflection 9 ms later
        e = offset + int(SR * 0.009)
        rec[e:e + len(click)] += click * 0.3
    return rec


@pytest.mark.parametrize("offset_ms", [12, 180, 420, 700])
def test_finds_click_within_half_a_millisecond(offset_ms):
    off = int(SR * offset_ms / 1000)
    found = find_click(fake_recording(off), make_click()[:, 0])
    assert found is not None
    idx, snr = found
    assert abs(idx - off) <= SR * 0.0005
    assert snr > 6


def test_rejects_pure_noise():
    rng = np.random.default_rng(3)
    assert find_click(rng.normal(0, 0.01, SR).astype(np.float32), make_click()[:, 0]) is None


def test_finds_click_under_loud_hum():
    off = int(SR * 0.25)
    rec = fake_recording(off, gain=0.05)
    t = np.arange(len(rec)) / SR
    rec += (0.05 * np.sin(2 * np.pi * 100 * t)).astype(np.float32)  # loud mains-style hum
    idx, _ = find_click(rec, make_click()[:, 0])
    assert abs(idx - off) <= SR * 0.0005


def test_suggest_delays_aligns_to_slowest():
    delays, clamped = suggest_delays({"bt1": 220.0, "bt2": 160.4, "wired": 12.0})
    assert delays == {"bt1": 0.0, "bt2": 60.0, "wired": 208.0}
    assert clamped == []


def test_suggest_delays_clamps_beyond_slider_range():
    delays, clamped = suggest_delays({"slow": 1500.0, "fast": 10.0})
    assert delays["fast"] == 1000.0
    assert clamped == ["fast"]


def test_suggest_delays_empty():
    assert suggest_delays({}) == ({}, [])


def test_consensus_outvotes_stray_detections():
    from backend.engine.calibration import consensus
    med, spread, n = consensus([98.0, 637.5, 100.8, 100.6, 12.0])
    assert n == 3 and spread == pytest.approx(2.8) and med == pytest.approx(100.6)


def test_consensus_fails_when_nothing_agrees():
    from backend.engine.calibration import consensus
    assert consensus([95.5, 200.1, -13.2, 322.0, 483.3]) is None
    assert consensus([100.0, 101.0]) is None  # too few
