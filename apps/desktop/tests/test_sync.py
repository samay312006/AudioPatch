"""Delay buffer and drift correction, simulated without audio hardware."""
import numpy as np
import pytest

from backend.engine.mixer import CH, SR, OutputMixer, Params, Route, SourceNode

BLOCK = 480  # 10 ms


def wire(src, out):
    r = Route(src.id, out.id)
    src.rings += (r.ring,)
    out.inputs += (r,)
    return r


def impulse_train(n_blocks, every=100):
    """Blocks of silence with a single-sample click at the start of every `every`-th block."""
    for b in range(n_blocks):
        x = np.zeros((BLOCK, CH), np.float32)
        if b % every == 0:
            x[0] = 1.0
        yield x


def first_click(signal, start=0):
    idx = np.flatnonzero(signal[start:, 0] > 0.5)
    return start + idx[0] if len(idx) else None


def run(src, outs, n_blocks):
    rendered = {o.id: [] for o in outs}
    for x in impulse_train(n_blocks):
        src.push(x)
        for o in outs:
            rendered[o.id].append(o.render(BLOCK).copy())
    return {k: np.concatenate(v) for k, v in rendered.items()}


@pytest.mark.parametrize("delay_ms", [0, 37, 120, 500])
def test_delay_shifts_output_exactly(delay_ms):
    src = SourceNode("s", Params())
    ref = OutputMixer("ref", Params(), target_ms=20)
    late = OutputMixer("late", Params(delay_ms=delay_ms), target_ms=20)
    wire(src, ref)
    wire(src, late)
    sig = run(src, [ref, late], 300)
    # Clicks repeat every second, and starting playback may trim the very first
    # one, so compare click positions modulo the click interval.
    a = first_click(sig["ref"], first_click(sig["ref"]) + 1)
    b = first_click(sig["late"], first_click(sig["late"]) + 1)
    assert (b - a) % (100 * BLOCK) == pytest.approx(SR * delay_ms / 1000, abs=1)


def test_delay_change_while_playing_converges():
    src = SourceNode("s", Params())
    ref = OutputMixer("ref", Params(), target_ms=20)
    p = Params()
    late = OutputMixer("late", p, target_ms=20)
    wire(src, ref)
    r = wire(src, late)
    run(src, [ref, late], 100)
    p.delay_ms = 200
    sig = run(src, [ref, late], 3000)  # 30 s for the controller to settle
    last_ref = np.flatnonzero(sig["ref"][:, 0] > 0.5)[-1]
    last_late = np.flatnonzero(sig["late"][:, 0] > 0.5)[-1]
    assert last_late - last_ref == pytest.approx(SR * 0.2, abs=SR * 0.002)  # within 2 ms
    p.delay_ms = 50  # decreasing jumps immediately
    sig = run(src, [ref, late], 300)
    last_ref = np.flatnonzero(sig["ref"][:, 0] > 0.5)[-1]
    last_late = np.flatnonzero(sig["late"][:, 0] > 0.5)[-1]
    assert last_late - last_ref == pytest.approx(SR * 0.05, abs=SR * 0.002)
    assert r.underruns == 0


@pytest.mark.parametrize("ppm", [-200, 200])
def test_drift_is_corrected_over_long_playback(ppm):
    """Source clock runs `ppm` fast/slow vs the output. Without correction 200 ppm
    drifts 48 ms in 4 minutes; with it the buffer (= latency) must stay put."""
    src = SourceNode("s", Params())
    out = OutputMixer("o", Params(), target_ms=40)
    r = wire(src, out)
    ratio = 1 + ppm * 1e-6
    rng = np.random.default_rng(1)
    produced = 0.0
    fills = []
    for b in range(24000):  # 4 minutes of 10 ms callbacks
        produced += BLOCK * ratio
        # Deliver in irregular packets, like a real capture device.
        while produced >= 1:
            k = int(min(produced, rng.integers(200, 700)))
            src.push(np.full((k, CH), 0.1, np.float32))
            produced -= k
        out.render(BLOCK)
        if b > 12000:  # measure the last 2 minutes, after the controller settles
            fills.append(r.ring.available)
    target = out.target_frames  # fill right after a read
    fills = np.array(fills)
    assert r.skips == 0 and r.underruns == 0
    # Mean latency within 1 ms of target over the last 2 minutes.
    assert abs(fills.mean() - target) < SR * 0.001
    # And not creeping: first vs last 20 s of that window agree within 1 ms.
    assert abs(fills[:2000].mean() - fills[-2000:].mean()) < SR * 0.001


def test_resampled_audio_stays_clean():
    """While correcting drift the output must be a smooth signal, not glitchy."""
    src = SourceNode("s", Params())
    out = OutputMixer("o", Params(), target_ms=40)
    wire(src, out)
    t = 0
    rendered = []
    for b in range(3000):
        n = int(BLOCK * 1.002) if b % 2 else BLOCK  # source ~0.1% fast
        tt = (np.arange(t, t + n) / SR).astype(np.float64)
        src.push(np.repeat((0.5 * np.sin(2 * np.pi * 440 * tt)).astype(np.float32)[:, None], CH, 1))
        t += n
        rendered.append(out.render(BLOCK).copy())
    y = np.concatenate(rendered)[SR * 10:, 0]  # after priming/settling
    # A 440 Hz sine at 0.5 changes by at most 2*pi*440/48000*0.5 ≈ 0.029 per sample.
    assert np.abs(np.diff(y)).max() < 0.035


def test_test_tone_ignores_mute_and_reports_start_time():
    out = OutputMixer("o", Params(muted=True), target_ms=10)
    tone = np.full((100, CH), 0.3, np.float32)
    out.play_tone(tone)
    y = out.render(BLOCK, when=123.0)
    assert y[:100, 0].max() == pytest.approx(0.3)
    assert out.tone_started_at == 123.0


def test_calibrating_silences_routes_but_not_tone():
    src = SourceNode("s", Params())
    out = OutputMixer("o", Params(), target_ms=10)
    wire(src, out)
    for _ in range(3):
        src.push(np.full((BLOCK, CH), 0.5, np.float32))
    out.calibrating = True
    out.play_tone(np.full((10, CH), 0.2, np.float32))
    y = out.render(BLOCK)
    assert y[:10, 0].max() == pytest.approx(0.2)
    assert np.all(y[10:] == 0)
