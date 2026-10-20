package com.audiopatch

import com.audiopatch.audio.CH
import com.audiopatch.audio.FloatRing
import com.audiopatch.audio.JitterReader
import com.audiopatch.link.BtProtocol
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs
import kotlin.math.sin
import kotlin.random.Random

class JitterBufferTest {
    private val sr = 48000

    @Test fun ringWrapsAndKeepsOrder() {
        val ring = FloatRing(10)
        val out = FloatArray(6 * CH)
        var v = 0f
        repeat(7) {
            val block = FloatArray(6 * CH) { i -> v + i / CH }
            assertEquals(6, ring.write(block, 6))
            assertEquals(6, ring.readInto(out, 0, 6))
            for (i in 0 until 6) assertEquals(v + i, out[i * CH], 0f)
            v += 6
        }
    }

    /** Same bytes as the desktop's protocol.py: type u8 | length u16 LE | payload. */
    @Test fun framingMatchesDesktopFormat() {
        val bytes = BtProtocol.encode(BtProtocol.AUDIO, byteArrayOf(7, 0, 0, 0, 0x11, 0x22))
        assertEquals(listOf(2, 6, 0, 7, 0, 0, 0, 0x11, 0x22), bytes.map { it.toInt() })
        val ping = BtProtocol.control(org.json.JSONObject().put("t", "ping"))
        assertEquals(1, ping[0].toInt())
        val stream = java.io.ByteArrayInputStream(bytes + bytes)
        val f = BtProtocol.read(stream)
        assertEquals(BtProtocol.AUDIO, f.type)
        assertEquals(7L, BtProtocol.audioSeq(f.payload))
        assertEquals(6, BtProtocol.read(stream).payload.size)
        assertThrows(java.io.IOException::class.java) {
            BtProtocol.read(java.io.ByteArrayInputStream(byteArrayOf(9, 1, 0, 0)))  // unknown type
        }
    }

    @Test fun silentUntilPrimedThenPassesAudio() {
        val ring = FloatRing(sr)
        val reader = JitterReader(ring, 480)
        val out = FloatArray(480 * CH)
        ring.write(FloatArray(480 * CH) { 0.5f }, 480)
        assertEquals(0, reader.read(out, 480))
        ring.write(FloatArray(480 * CH) { 0.5f }, 480)
        assertEquals(480, reader.read(out, 480))
        assertTrue(out.all { it == 0.5f })
    }

    /** Sender clock 200 ppm fast/slow, Wi-Fi-like bursty arrival: latency must hold steady. */
    @Test fun driftIsCorrected() {
        for (ppm in intArrayOf(-200, 200)) {
            val ring = FloatRing(sr * 2)
            val target = sr * 80 / 1000
            val reader = JitterReader(ring, target)
            val rng = Random(1)
            val out = FloatArray(240 * CH)
            var produced = 0.0
            val fills = ArrayList<Int>()
            for (b in 0 until 48000) {               // 4 minutes of 5 ms reads
                produced += 240 * (1 + ppm * 1e-6)
                while (produced >= 1) {
                    val k = minOf(produced.toInt(), 120 + rng.nextInt(600))
                    ring.write(FloatArray(k * CH) { 0.1f }, k)
                    produced -= k
                }
                reader.read(out, 240)
                if (b > 24000) fills.add(ring.available)
            }
            assertEquals("skips at $ppm ppm", 0, reader.skips)
            assertEquals("underruns at $ppm ppm", 0, reader.underruns)
            val mean = fills.average()
            assertTrue("mean fill $mean vs $target at $ppm ppm", abs(mean - target) < sr * 0.002)
        }
    }

    @Test fun resampledAudioStaysSmooth() {
        val ring = FloatRing(sr * 2)
        val reader = JitterReader(ring, 480 * 4)
        val out = FloatArray(480 * CH)
        val ys = ArrayList<Float>()
        var t = 0
        for (b in 0 until 3000) {
            val n = if (b % 2 == 1) (480 * 1.002).toInt() else 480     // sender ~0.1% fast
            val block = FloatArray(n * CH) { i -> (0.5 * sin(2 * Math.PI * 440 * (t + i / CH) / sr)).toFloat() }
            t += n
            ring.write(block, n)
            reader.read(out, 480)
            if (b > 1000) for (f in 0 until 480) ys.add(out[f * CH])
        }
        var maxStep = 0f
        for (i in 1 until ys.size) maxStep = maxOf(maxStep, abs(ys[i] - ys[i - 1]))
        assertTrue("max sample step $maxStep", maxStep < 0.035f)  // 440 Hz at 0.5: <= 0.029
    }
}
