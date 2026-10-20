import numpy as np

from backend.engine.ringbuffer import RingBuffer
from backend.engine.resampler import StreamResampler


def frames(start, n, ch=2):
    return np.repeat(np.arange(start, start + n, dtype=np.float32)[:, None], ch, axis=1)


def test_write_read_roundtrip_with_wraparound():
    rb = RingBuffer(10)
    out = np.zeros((10, 2), np.float32)
    pos = 0
    for _ in range(7):  # forces many wraps
        assert rb.write(frames(pos, 6)) == 6
        assert rb.read_into(out[:6]) == 6
        np.testing.assert_array_equal(out[:6], frames(pos, 6))
        pos += 6
    assert rb.available == 0


def test_partial_read_when_underrun():
    rb = RingBuffer(16)
    rb.write(frames(0, 3))
    out = np.zeros((8, 2), np.float32)
    assert rb.read_into(out) == 3
    np.testing.assert_array_equal(out[:3], frames(0, 3))


def test_overflow_drops_newest_and_counts():
    rb = RingBuffer(8)
    assert rb.write(frames(0, 6)) == 6
    assert rb.write(frames(6, 6)) == 2
    assert rb.overruns == 1
    out = np.zeros((8, 2), np.float32)
    rb.read_into(out)
    np.testing.assert_array_equal(out, frames(0, 8))


def test_skip_bounds_latency():
    rb = RingBuffer(32)
    rb.write(frames(0, 20))
    rb.skip(15)
    out = np.zeros((5, 2), np.float32)
    assert rb.read_into(out) == 5
    np.testing.assert_array_equal(out, frames(15, 5))
    rb.skip(100)  # never past the write head
    assert rb.available == 0


def test_resampler_identity_passthrough():
    r = StreamResampler(1.0)
    x = frames(0, 64)
    np.testing.assert_array_equal(r.process(x), x)


def test_resampler_rate_is_exact_over_many_blocks():
    # 44.1k -> 48k: output length must track the ratio with no cumulative error.
    r = StreamResampler(48000 / 44100)
    total_in = total_out = 0
    for n in [441, 512, 100, 1000, 7] * 40:
        total_out += len(r.process(np.zeros((n, 2), np.float32)))
        total_in += n
    assert abs(total_out - total_in * 48000 / 44100) <= 1


def test_resampler_is_continuous_across_blocks():
    # A ramp resampled in chunks must stay a ramp (no glitches at boundaries).
    r = StreamResampler(1.001)
    ramp = frames(0, 3000)
    out = np.concatenate([r.process(ramp[i:i + 128]) for i in range(0, 3000, 128)])
    diffs = np.diff(out[2:, 0])  # first output interpolates from the zero initial state
    np.testing.assert_allclose(diffs, 1 / 1.001, atol=1e-3)
