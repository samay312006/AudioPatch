import numpy as np

from backend.engine.mixer import CH, OutputMixer, Params, Route, SourceNode, make_chime


def const(v, n=480):
    return np.full((n, CH), v, np.float32)


def wire(source: SourceNode, out: OutputMixer) -> Route:
    r = Route(source.id, out.id)
    source.rings = source.rings + (r.ring,)
    out.inputs = out.inputs + (r,)
    return r


def setup(target_ms=10.0):
    src = SourceNode("s", Params())
    out = OutputMixer("o", Params(), target_ms=target_ms)
    route = wire(src, out)
    return src, out, route


def test_silent_until_primed_then_passes_audio():
    src, out, _ = setup(target_ms=10)  # 480 frames cushion
    src.push(const(0.5, 480))
    assert np.all(out.render(480) == 0)  # needs target + n buffered
    src.push(const(0.5, 480))
    np.testing.assert_allclose(out.render(480), 0.5)


def test_mixes_multiple_sources():
    a, out, _ = setup()
    b = SourceNode("b", Params())
    wire(b, out)
    for _ in range(3):
        a.push(const(0.2))
        b.push(const(0.3))
    np.testing.assert_allclose(out.render(480), 0.5, rtol=1e-6)


def test_source_fans_out_to_two_outputs():
    src = SourceNode("s", Params())
    o1, o2 = OutputMixer("o1", Params(), 10), OutputMixer("o2", Params(), 10)
    wire(src, o1)
    wire(src, o2)
    for _ in range(3):
        src.push(const(0.4))
    np.testing.assert_allclose(o1.render(480), 0.4)
    np.testing.assert_allclose(o2.render(480), 0.4)


def test_volumes_and_mute():
    src, out, _ = setup()
    src.params.volume = 0.5
    out.params.volume = 0.5
    for _ in range(3):
        src.push(const(0.8))
    np.testing.assert_allclose(out.render(480), 0.2, rtol=1e-6)
    out.params.muted = True
    assert np.all(out.render(480) == 0)


def test_mono_downmix():
    src, out, _ = setup()
    src.params.mono = True
    block = np.zeros((480, 2), np.float32)
    block[:, 0] = 0.6  # left only
    for _ in range(3):
        src.push(block)
    np.testing.assert_allclose(out.render(480), 0.3, rtol=1e-6)


def test_clips_instead_of_wrapping():
    a, out, _ = setup()
    b = SourceNode("b", Params())
    wire(b, out)
    for _ in range(3):
        a.push(const(0.9))
        b.push(const(0.9))
    assert out.render(480).max() == 1.0


def test_underrun_reprimes_and_counts():
    src, out, route = setup()
    for _ in range(2):
        src.push(const(0.5))
    out.render(480)
    out.render(480)  # drains the cushion
    out.render(480)  # empty -> underrun
    assert route.underruns >= 1
    assert route.primed is False


def test_latency_is_bounded_when_source_runs_ahead():
    src, out, route = setup(target_ms=10)
    for _ in range(40):  # 400 ms of backlog
        src.push(const(0.1))
    out.render(480)
    # after catching up, at most target buffered
    assert route.ring.available <= out.target_frames


def test_test_tone_plays_once_without_sources():
    out = OutputMixer("o", Params(), 10)
    chime = make_chime()
    out.play_tone(chime)
    rendered = np.concatenate([out.render(480).copy() for _ in range(len(chime) // 480 + 2)])
    assert np.abs(rendered).max() > 0.1
    assert np.all(out.render(480) == 0)  # finished


def test_level_meter_reports_peak_then_resets():
    src, out, _ = setup()
    for _ in range(3):
        src.push(const(0.7))
    out.render(480)
    assert abs(out.meter.take() - 0.7) < 1e-6
    assert out.meter.take() == 0.0
