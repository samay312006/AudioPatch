package com.audiopatch.modes

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.projection.MediaProjection
import android.os.Process
import android.util.Log
import androidx.annotation.RequiresPermission
import com.audiopatch.audio.CH
import com.audiopatch.audio.FloatRing
import com.audiopatch.audio.JitterReader
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.sin

/** A phone output the router can play to. */
data class PhoneOutput(val id: Int, val name: String, val type: Int) {
    val label: String get() = when (type) {
        AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> "Phone speaker"
        AudioDeviceInfo.TYPE_WIRED_HEADPHONES, AudioDeviceInfo.TYPE_WIRED_HEADSET -> "Wired headphones"
        AudioDeviceInfo.TYPE_USB_HEADSET, AudioDeviceInfo.TYPE_USB_DEVICE -> "USB audio"
        AudioDeviceInfo.TYPE_HDMI -> "HDMI"
        else -> name.ifBlank { "Audio device" }
    }
    val kind: String get() = when (type) {
        AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, AudioDeviceInfo.TYPE_BLE_HEADSET,
        AudioDeviceInfo.TYPE_BLE_SPEAKER, AudioDeviceInfo.TYPE_HEARING_AID -> "Bluetooth"
        AudioDeviceInfo.TYPE_USB_HEADSET, AudioDeviceInfo.TYPE_USB_DEVICE -> "USB"
        AudioDeviceInfo.TYPE_WIRED_HEADPHONES, AudioDeviceInfo.TYPE_WIRED_HEADSET -> "Wired"
        else -> "Built-in"
    }

    companion object {
        /** Output types that make sense for media (no telephony, FM, bus, etc.). */
        // (No earpiece: Android reserves it for calls and reroutes media to the speaker.)
        private val MEDIA_SINKS = setOf(
            AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
            AudioDeviceInfo.TYPE_WIRED_HEADPHONES, AudioDeviceInfo.TYPE_WIRED_HEADSET,
            AudioDeviceInfo.TYPE_USB_HEADSET, AudioDeviceInfo.TYPE_USB_DEVICE,
            AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, AudioDeviceInfo.TYPE_BLE_HEADSET,
            AudioDeviceInfo.TYPE_BLE_SPEAKER, AudioDeviceInfo.TYPE_HEARING_AID,
            AudioDeviceInfo.TYPE_HDMI,
        )

        fun list(ctx: Context): List<PhoneOutput> =
            ctx.getSystemService(AudioManager::class.java).getDevices(AudioManager.GET_DEVICES_OUTPUTS)
                .filter { it.type in MEDIA_SINKS }
                .map { PhoneOutput(it.id, it.productName?.toString().orEmpty(), it.type) }
                .distinctBy { it.id }
    }
}

data class RouterOutputStatus(
    val output: PhoneOutput,
    val volume: Float,
    val delayMs: Int,
    val routedTo: String?,     // where Android actually sent this stream (null until playing)
    val honoured: Boolean,     // routedTo is the device we asked for
    val level: Float,
    val underruns: Int,
)

data class RouterStatus(
    val running: Boolean = false,
    val source: String = "",           // "phone audio" or "test tone"
    val captureLevel: Float = 0f,
    val outputs: List<RouterOutputStatus> = emptyList(),
    val error: String? = null,
)

/**
 * Router mode: plays this phone's audio on several of its outputs at once.
 *
 * Source: AudioPlaybackCapture of other apps' audio (Android 10+, needs the
 * screen-capture consent; apps can opt out), or a test tone. Our own playback
 * is excluded from capture, otherwise the copies would be captured again.
 *
 * Each output gets its own AudioTrack with a *preferred* device. Android may
 * override that (most phones send all media to one output), so every output
 * reports where its stream was actually routed instead of assuming.
 */
class Router(private val ctx: Context) {
    companion object {
        const val SR = 48000
        const val BASE_MS = 40       // same phone clock on both sides: a small cushion is enough
        private const val TAG = "AudioPatch"
    }

    private val _status = MutableStateFlow(RouterStatus())
    val status: StateFlow<RouterStatus> = _status.asStateFlow()

    private class Out(
        val output: PhoneOutput,
        @Volatile var volume: Float,
        @Volatile var delayMs: Int,
    ) {
        val ring = FloatRing(SR * 2)
        val reader = JitterReader(ring, SR * (BASE_MS + delayMs) / 1000)
        @Volatile var peak = 0f
        @Volatile var routedTo: String? = null
        @Volatile var honoured = false
        var thread: Thread? = null
    }

    @Volatile private var running = false
    private val outs = CopyOnWriteArrayList<Out>()
    private var captureThread: Thread? = null
    @Volatile private var capturePeak = 0f

    /** Start with the phone's own audio as the source. */
    @RequiresPermission(Manifest.permission.RECORD_AUDIO)
    fun startCapture(projection: MediaProjection, outputs: List<PhoneOutput>) {
        val config = AudioPlaybackCaptureConfiguration.Builder(projection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .excludeUid(Process.myUid())   // never capture our own copies (feedback loop)
            .build()
        val format = AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
            .setSampleRate(SR).setChannelMask(AudioFormat.CHANNEL_IN_STEREO).build()
        val rec = AudioRecord.Builder()
            .setAudioFormat(format)
            .setBufferSizeInBytes(SR / 10 * CH * 4)
            .setAudioPlaybackCaptureConfig(config)
            .build()
        start("phone audio", outputs) { buf, n ->
            if (rec.recordingState != AudioRecord.RECORDSTATE_RECORDING) rec.startRecording()
            val got = rec.read(buf, 0, n * CH, AudioRecord.READ_BLOCKING)
            if (got <= 0) 0 else got / CH
        }
        onStop = { try { rec.stop() } catch (_: Exception) {}; rec.release() }
    }

    /** Start with a test tone as the source (debug / "test outputs"). */
    fun startTone(outputs: List<PhoneOutput>) {
        var phase = 0.0
        var produced = 0L
        val t0 = System.nanoTime()
        start("test tone", outputs) { buf, n ->
            for (i in 0 until n) {
                val v = (0.2 * sin(phase)).toFloat()
                buf[2 * i] = v; buf[2 * i + 1] = v
                phase += 2 * PI * 440 / SR
            }
            // Pace against absolute time like a real source; sleep() alone oversleeps
            // and would slowly starve the outputs.
            produced += n
            val dueNs = t0 + produced * 1_000_000_000L / SR
            val waitMs = (dueNs - System.nanoTime()) / 1_000_000
            if (waitMs > 0) Thread.sleep(waitMs)
            n
        }
    }

    private var onStop: (() -> Unit)? = null

    private fun start(source: String, outputs: List<PhoneOutput>, read: (FloatArray, Int) -> Int) {
        stop()
        running = true
        outputs.forEach { addOutput(it) }
        _status.value = RouterStatus(running = true, source = source)
        captureThread = Thread({
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
            val n = SR / 100      // 10 ms
            val buf = FloatArray(n * CH)
            var tick = 0
            try {
                while (running) {
                    val got = read(buf, n)
                    if (got <= 0) continue
                    for (i in 0 until got * CH) capturePeak = max(capturePeak, abs(buf[i]))
                    for (o in outs) o.ring.write(buf, got)
                    if (++tick % 10 == 0) publish()
                }
            } catch (e: Exception) {
                Log.w(TAG, "router source stopped", e)
                if (running) _status.update { it.copy(error = "Audio capture stopped: ${e.message}") }
            }
        }, "router-source").apply { isDaemon = true; start() }
    }

    fun addOutput(o: PhoneOutput, volume: Float = 1f, delayMs: Int = 0) {
        if (outs.any { it.output.id == o.id }) return
        val out = Out(o, volume, delayMs)
        outs.add(out)
        if (running) out.thread = Thread({ playLoop(out) }, "router-out-${o.id}").apply { isDaemon = true; start() }
        publish()
    }

    fun removeOutput(id: Int) {
        outs.removeAll { it.output.id == id }   // its play loop sees it's gone and stops
        publish()
    }

    fun setVolume(id: Int, v: Float) { outs.find { it.output.id == id }?.volume = v.coerceIn(0f, 2f); publish() }

    fun setDelay(id: Int, ms: Int) {
        outs.find { it.output.id == id }?.let {
            it.delayMs = ms.coerceIn(0, 1000)
            it.reader.targetFrames = SR * (BASE_MS + it.delayMs) / 1000
        }
        publish()
    }

    fun stop() {
        running = false
        captureThread?.join(500)
        outs.forEach { it.thread?.join(500) }
        outs.clear()
        onStop?.invoke(); onStop = null
        _status.value = RouterStatus()
    }

    @SuppressLint("WrongConstant")
    private fun playLoop(out: Out) {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        val am = ctx.getSystemService(AudioManager::class.java)
        val device = am.getDevices(AudioManager.GET_DEVICES_OUTPUTS).firstOrNull { it.id == out.output.id }
        val minBuf = AudioTrack.getMinBufferSize(SR, AudioFormat.CHANNEL_OUT_STEREO, AudioFormat.ENCODING_PCM_FLOAT)
        val track = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
            .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
                .setSampleRate(SR).setChannelMask(AudioFormat.CHANNEL_OUT_STEREO).build())
            .setBufferSizeInBytes(max(minBuf, SR / 50 * CH * 4))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        if (device != null) track.setPreferredDevice(device)
        val n = 240
        val buf = FloatArray(n * CH)
        var tick = 0
        track.play()
        try {
            while (running && outs.contains(out)) {
                out.reader.read(buf, n)
                val g = out.volume
                for (i in buf.indices) { buf[i] *= g; out.peak = max(out.peak, abs(buf[i])) }
                track.write(buf, 0, buf.size, AudioTrack.WRITE_BLOCKING)
                if (++tick % 40 == 0) {   // every ~200 ms: where did Android really send it?
                    val routed = track.routedDevice
                    out.routedTo = routed?.let { PhoneOutput(it.id, it.productName?.toString().orEmpty(), it.type).label }
                    out.honoured = routed?.id == out.output.id
                }
            }
        } finally {
            track.stop()
            track.release()
        }
    }

    private fun publish() {
        val list = outs.map {
            val s = RouterOutputStatus(it.output, it.volume, it.delayMs, it.routedTo, it.honoured, it.peak, it.reader.underruns)
            it.peak = 0f
            s
        }
        val cap = capturePeak
        capturePeak = 0f
        _status.update { it.copy(outputs = list, captureLevel = cap) }
        if (list.isNotEmpty() && list.any { o -> o.routedTo != null }) {
            Log.i(TAG, "router: " + list.joinToString { o ->
                "${o.output.label}[${o.output.id}]->${o.routedTo}${if (o.honoured) "" else " (OVERRIDDEN)"} lvl=${"%.2f".format(o.level)} ur=${o.underruns}"
            })
        }
    }
}
