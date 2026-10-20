package com.audiopatch.audio

import android.media.MediaCodec
import android.media.MediaFormat
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Opus -> PCM using Android's built-in decoder (MediaCodec, Android 10+).
 * Synchronous use: decode() one 20 ms packet, get interleaved stereo floats back.
 */
class OpusDecoder(private val sampleRate: Int = 48000, private val channels: Int = 2) {
    private val codec: MediaCodec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS)
    private val info = MediaCodec.BufferInfo()
    private var ptsUs = 0L
    private var out = FloatArray(960 * channels * 2)

    init {
        val format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, sampleRate, channels)
        // Opus needs its identification header (csd-0) plus pre-skip and seek
        // pre-roll in nanoseconds (csd-1, csd-2), as in an Ogg Opus stream.
        val preSkip = 312
        val head = ByteBuffer.allocate(19).order(ByteOrder.LITTLE_ENDIAN)
            .put("OpusHead".toByteArray()).put(1).put(channels.toByte())
            .putShort(preSkip.toShort()).putInt(sampleRate).putShort(0).put(0)
        head.flip()
        format.setByteBuffer("csd-0", head)
        format.setByteBuffer("csd-1", nanos(preSkip * 1_000_000_000L / sampleRate))
        format.setByteBuffer("csd-2", nanos(80_000_000L))
        codec.configure(format, null, null, 0)
        codec.start()
    }

    private fun nanos(v: Long): ByteBuffer =
        ByteBuffer.allocate(8).order(ByteOrder.nativeOrder()).putLong(v).also { it.flip() }

    /**
     * Decode one packet; [sink] receives (interleaved stereo samples, frame count)
     * for each chunk of PCM the decoder produces. The first packet may produce none.
     */
    fun decode(packet: ByteArray, offset: Int, length: Int, sink: (FloatArray, Int) -> Unit) {
        val inIdx = codec.dequeueInputBuffer(20_000)
        if (inIdx >= 0) {
            val buf = codec.getInputBuffer(inIdx)!!
            buf.clear()
            buf.put(packet, offset, length)
            codec.queueInputBuffer(inIdx, 0, length, ptsUs, 0)
            ptsUs += 20_000
        }
        while (true) {
            val outIdx = codec.dequeueOutputBuffer(info, 0)
            if (outIdx < 0) break   // INFO_TRY_AGAIN_LATER / format or buffer changes
            val ob = codec.getOutputBuffer(outIdx)!!.order(ByteOrder.LITTLE_ENDIAN)
            val samples = info.size / 2
            if (out.size < samples) out = FloatArray(samples * 2)
            ob.position(info.offset)
            val sb = ob.asShortBuffer()
            for (i in 0 until samples) out[i] = sb.get(i) / 32768f
            codec.releaseOutputBuffer(outIdx, false)
            if (samples > 0) sink(out, samples / channels)
        }
    }

    fun release() {
        try { codec.stop() } catch (_: Exception) {}
        codec.release()
    }
}
