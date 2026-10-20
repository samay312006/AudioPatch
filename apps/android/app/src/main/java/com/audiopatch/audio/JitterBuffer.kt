package com.audiopatch.audio

import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min

const val CH = 2

/**
 * Single-producer / single-consumer ring of interleaved stereo floats.
 * The producer only advances [written], the consumer only advances [read].
 */
class FloatRing(val capacityFrames: Int) {
    private val buf = FloatArray(capacityFrames * CH)
    @Volatile private var written = 0L
    @Volatile private var read = 0L
    @Volatile var overruns = 0
        private set

    val available: Int get() = (written - read).toInt()

    fun write(src: FloatArray, frames: Int, srcOffsetFrames: Int = 0): Int {
        val n = min(frames, capacityFrames - available)
        if (n < frames) overruns++
        if (n <= 0) return 0
        var w = (written % capacityFrames).toInt()
        var s = srcOffsetFrames
        var left = n
        while (left > 0) {
            val chunk = min(left, capacityFrames - w)
            System.arraycopy(src, s * CH, buf, w * CH, chunk * CH)
            w = (w + chunk) % capacityFrames
            s += chunk
            left -= chunk
        }
        written += n
        return n
    }

    fun readInto(dst: FloatArray, dstOffsetFrames: Int, frames: Int): Int {
        val n = min(frames, available)
        if (n <= 0) return 0
        var r = (read % capacityFrames).toInt()
        var d = dstOffsetFrames
        var left = n
        while (left > 0) {
            val chunk = min(left, capacityFrames - r)
            System.arraycopy(buf, r * CH, dst, d * CH, chunk * CH)
            r = (r + chunk) % capacityFrames
            d += chunk
            left -= chunk
        }
        read += n
        return n
    }

    fun skip(frames: Int) {
        read += max(0, min(frames, available))
    }
}

/**
 * Consumer side of a network stream: holds the ring at [targetFrames] of
 * buffered audio (the jitter cushion) and cancels clock drift between sender
 * and this device by resampling by at most +/-0.3%.
 *
 * A direct port of the desktop mixer's Route (apps/desktop/backend/engine/mixer.py),
 * with the same constants, so both ends behave identically.
 */
class JitterReader(private val ring: FloatRing, var targetFrames: Int) {
    companion object {
        const val KP = 2e-6        // speed change per frame of fill error (~10 s)
        const val KI = 1e-9        // per read (~20 s integral time at 5-10 ms reads)
        const val MAX_DEV = 0.003  // +/-0.3%: about 5 cents of pitch, inaudible
        const val EMA = 0.02
    }

    var primed = false; private set
    var underruns = 0; private set
    var skips = 0; private set
    var step = 1.0; private set
    var fillAvg = 0.0; private set
    private var hasAvg = false
    private var integ = 0.0
    private var pos = 0.0
    private val prev = FloatArray(CH)
    private var src = FloatArray(1)

    private val slackFrames get() = max(targetFrames, 48000 * 80 / 1000)

    fun reset() {
        primed = false; hasAvg = false; integ = 0.0; step = 1.0; pos = 0.0
        prev.fill(0f)
    }

    /** Fill out[0 until n*CH]. Returns how many frames were real audio (rest is silence). */
    fun read(out: FloatArray, n: Int): Int {
        val target = targetFrames + n
        var avail = ring.available
        if (!primed) {
            if (avail < target) {
                out.fill(0f, 0, n * CH)
                return 0
            }
            if (avail > target) { ring.skip(avail - target); avail = target }
            primed = true
        }
        if (avail > target + slackFrames) {   // way behind: jump instead of drifting late
            ring.skip(avail - target)
            skips++
            hasAvg = false
            avail = target
        }
        fillAvg = if (!hasAvg) avail.toDouble() else fillAvg + EMA * (avail - fillAvg)
        hasAvg = true
        val err = fillAvg - target
        integ = (integ + KI * err).coerceIn(-MAX_DEV, MAX_DEV)
        step = 1.0 + (KP * err + integ).coerceIn(-MAX_DEV, MAX_DEV)
        val got = pull(out, n, step)
        if (got < n) {
            underruns++
            reset()
            out.fill(0f, got * CH, n * CH)
        }
        return got
    }

    private fun pull(out: FloatArray, n: Int, step: Double): Int {
        if (step == 1.0 && pos == 0.0) {
            val got = ring.readInto(out, 0, n)
            if (got > 0) { prev[0] = out[(got - 1) * CH]; prev[1] = out[(got - 1) * CH + 1] }
            return got
        }
        val last = pos + step * (n - 1)
        val need = ceil(last).toInt()           // new frames required beyond prev
        if (need > ring.available) return 0
        if (src.size < (need + 1) * CH) src = FloatArray((need + 1) * CH * 2)
        src[0] = prev[0]; src[1] = prev[1]
        ring.readInto(src, 1, need)
        for (k in 0 until n) {
            val t = pos + step * k
            val i = t.toInt()
            val f = (t - i).toFloat()
            val j = min(i + 1, need)
            out[k * CH] = src[i * CH] * (1 - f) + src[j * CH] * f
            out[k * CH + 1] = src[i * CH + 1] * (1 - f) + src[j * CH + 1] * f
        }
        pos = max(0.0, last + step - need)
        prev[0] = src[need * CH]; prev[1] = src[need * CH + 1]
        return n
    }
}
