package com.audiopatch.modes

import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import kotlin.math.PI
import kotlin.math.sin

/**
 * Debug experiment (debug builds only, via `--es alarm_tone <seconds>`):
 * does a USAGE_ALARM stream play on the speaker AND the Bluetooth headset at
 * once? Android's default policy duplicates alarms to both, which would be the
 * one no-permission way to get two outputs simultaneously.
 *
 * Logs every device Android reports the track on. getRoutedDevices() (API 36)
 * is called reflectively because the app compiles against API 35.
 */
object AlarmRouteTest {
    private const val TAG = "AudioPatch"

    fun run(seconds: Int, amplitude: Float = 0.06f) = Thread({
        val sr = 48000
        val track = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
            .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
                .setSampleRate(sr).setChannelMask(AudioFormat.CHANNEL_OUT_STEREO).build())
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        val n = sr / 50
        val buf = FloatArray(n * 2)
        var phase = 0.0
        track.play()
        val end = System.currentTimeMillis() + seconds * 1000L
        var lastLog = 0L
        while (System.currentTimeMillis() < end) {
            for (i in 0 until n) {
                val v = (amplitude * sin(phase)).toFloat()
                buf[2 * i] = v; buf[2 * i + 1] = v
                phase += 2 * PI * 660 / sr
            }
            track.write(buf, 0, buf.size, AudioTrack.WRITE_BLOCKING)
            val now = System.currentTimeMillis()
            if (now - lastLog > 700) {
                lastLog = now
                Log.i(TAG, "alarm test: routedDevice=${describe(track.routedDevice)} routedDevices=${routedDevices(track)}")
            }
        }
        track.stop()
        track.release()
        Log.i(TAG, "alarm test: done")
    }, "alarm-route-test").apply { isDaemon = true; start() }

    private fun routedDevices(track: AudioTrack): String = try {
        @Suppress("UNCHECKED_CAST")
        val list = AudioTrack::class.java.getMethod("getRoutedDevices").invoke(track) as List<AudioDeviceInfo>
        list.joinToString(prefix = "[", postfix = "]") { describe(it) }
    } catch (e: Exception) {
        "n/a"
    }

    private fun describe(d: AudioDeviceInfo?): String = when (d?.type) {
        null -> "none"
        AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> "speaker"
        AudioDeviceInfo.TYPE_BUILTIN_SPEAKER_SAFE -> "speaker(safe)"
        AudioDeviceInfo.TYPE_BLUETOOTH_A2DP -> "A2DP:${d.productName}"
        else -> "type${d.type}:${d.productName}"
    }
}
