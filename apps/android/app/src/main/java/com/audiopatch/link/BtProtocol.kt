package com.audiopatch.link

import org.json.JSONObject
import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.util.UUID

/**
 * AudioPatch Bluetooth link (v1). Mirrors apps/desktop/backend/bt/protocol.py;
 * spec in docs/bluetooth-link.md.
 *
 * One RFCOMM connection, a sequence of frames: type (u8) | length (u16 LE) | payload.
 *   type 1 CONTROL: UTF-8 JSON object
 *   type 2 AUDIO:   seq (u32 LE) + one Opus packet (20 ms, 48 kHz stereo)
 */
object BtProtocol {
    const val VERSION = 1
    val SERVICE_UUID: UUID = UUID.fromString("6b1f1d2e-3c1a-4f7e-9a51-a0d10b0a7c11")

    const val SAMPLE_RATE = 48000
    const val CHANNELS = 2
    const val FRAME_SAMPLES = 960      // 20 ms per channel
    const val CONTROL = 1
    const val AUDIO = 2
    const val MAX_PAYLOAD = 4096
    const val PING_INTERVAL_MS = 1000L

    class Frame(val type: Int, val payload: ByteArray)

    fun encode(type: Int, payload: ByteArray): ByteArray {
        require(payload.size <= MAX_PAYLOAD) { "payload too large" }
        val out = ByteArray(3 + payload.size)
        out[0] = type.toByte()
        out[1] = (payload.size and 0xFF).toByte()
        out[2] = (payload.size shr 8 and 0xFF).toByte()
        System.arraycopy(payload, 0, out, 3, payload.size)
        return out
    }

    fun control(msg: JSONObject): ByteArray = encode(CONTROL, msg.toString().toByteArray(Charsets.UTF_8))

    fun write(out: OutputStream, msg: JSONObject) {
        synchronized(out) { out.write(control(msg)); out.flush() }
    }

    /** Blocking read of exactly one frame. Throws on EOF or a corrupt stream. */
    fun read(input: InputStream): Frame {
        val header = readFully(input, 3)
        val type = header[0].toInt() and 0xFF
        val len = (header[1].toInt() and 0xFF) or ((header[2].toInt() and 0xFF) shl 8)
        if ((type != CONTROL && type != AUDIO) || len > MAX_PAYLOAD) throw IOException("corrupt stream")
        return Frame(type, readFully(input, len))
    }

    /** seq and Opus packet of an AUDIO frame. */
    fun audioSeq(p: ByteArray): Long =
        (p[0].toLong() and 0xFF) or ((p[1].toLong() and 0xFF) shl 8) or
            ((p[2].toLong() and 0xFF) shl 16) or ((p[3].toLong() and 0xFF) shl 24)

    private fun readFully(input: InputStream, n: Int): ByteArray {
        val buf = ByteArray(n)
        var off = 0
        while (off < n) {
            val r = input.read(buf, off, n - off)
            if (r < 0) throw EOFException()
            off += r
        }
        return buf
    }
}
