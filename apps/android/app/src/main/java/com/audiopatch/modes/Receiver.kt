package com.audiopatch.modes

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothServerSocket
import android.bluetooth.BluetoothSocket
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Build
import android.os.Process
import android.os.SystemClock
import android.util.Log
import com.audiopatch.audio.CH
import com.audiopatch.audio.FloatRing
import com.audiopatch.audio.JitterReader
import com.audiopatch.audio.OpusDecoder
import com.audiopatch.link.BtProtocol
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONObject
import java.io.IOException
import kotlin.math.abs
import kotlin.math.max

data class ReceiverStatus(
    val waiting: Boolean = false,       // listening for the PC to connect
    val pc: String? = null,             // PC name once connected
    val streaming: Boolean = false,     // the PC added this phone and is sending audio
    val bufferMs: Int = 0,
    val underruns: Int = 0,
    val level: Float = 0f,
    val error: String? = null,
)

/**
 * "Wireless output" mode: waits for AudioPatch on a PC to connect over
 * Bluetooth (RFCOMM), decodes the Opus stream and plays it on this phone's
 * current output (speaker, wired or Bluetooth headphones).
 *
 * The PC connects to the phone rather than the other way round: on the PCs we
 * tested, a phone paging the PC times out unless a link already exists, while
 * the PC reaching the phone is quick and reliable.
 */
class Receiver(private val phoneId: String, private val phoneName: String) {
    companion object {
        // Measured on a real link: no packets lost, but RFCOMM goes quiet for up
        // to ~150 ms at a time (radio shared with the phone's own headphones), and
        // the fill swings ~+/-100 ms around its average. 100 ms ran dry ~1/s and
        // 200 ms ~1/15 s; 300 ms keeps the dips above the worst gaps. Underruns
        // still raise it (adaptive), and latency is otherwise fixed so sync holds.
        const val TARGET_MS = 300
    }

    private val _status = MutableStateFlow(ReceiverStatus())
    val status: StateFlow<ReceiverStatus> = _status.asStateFlow()

    @Volatile private var running = false
    private var socket: BluetoothSocket? = null
    private var server: BluetoothServerSocket? = null
    private val threads = java.util.concurrent.CopyOnWriteArrayList<Thread>()
    @Volatile private var resetPending = false  // set by the network thread, applied by the audio thread
    private val ring = FloatRing(BtProtocol.SAMPLE_RATE * 2)
    private val reader = JitterReader(ring, BtProtocol.SAMPLE_RATE * TARGET_MS / 1000, adaptive = true)

    @SuppressLint("MissingPermission")  // BLUETOOTH_CONNECT is requested before starting
    fun start(adapter: BluetoothAdapter) {
        if (running) return
        running = true
        _status.value = ReceiverStatus(waiting = true)
        thread("bt-listen") {
            // Self-healing: Bluetooth events (radio resets, link losses, Bluetooth
            // being switched off) can close the listening socket itself. Seen on a
            // real phone. Instead of giving up, re-open it and keep waiting.
            while (running) {
                try {
                    if (!adapter.isEnabled) {
                        _status.value = ReceiverStatus(waiting = true, error = "Bluetooth is off. Turn it on to continue.")
                        Thread.sleep(2000)
                        continue
                    }
                    val ss = adapter.listenUsingRfcommWithServiceRecord("AudioPatch", BtProtocol.SERVICE_UUID)
                    server = ss
                    _status.value = ReceiverStatus(waiting = true)
                    while (running) {
                        val s = ss.accept()          // blocks until the PC connects
                        socket = s
                        try {
                            session(s)
                        } catch (e: Exception) {
                            // PC went away; keep waiting so it can reconnect
                        } finally {
                            try { s.close() } catch (_: Exception) {}
                            if (running) _status.value = ReceiverStatus(waiting = true)
                        }
                    }
                } catch (e: IOException) {
                    if (!running) break
                    Log.i("AudioPatch", "listener closed (${e.message}); re-opening")
                    try { server?.close() } catch (_: Exception) {}
                    try { Thread.sleep(2000) } catch (_: InterruptedException) { break }
                } catch (_: InterruptedException) {
                    break
                }
            }
        }
    }

    fun stop() {
        if (!running) return
        running = false
        socket?.let { s ->
            try { BtProtocol.write(s.outputStream, JSONObject().put("t", "bye")) } catch (_: Exception) {}
            try { s.close() } catch (_: Exception) {}
        }
        try { server?.close() } catch (_: Exception) {}
        threads.forEach { it.interrupt() }
        threads.clear()
        _status.value = ReceiverStatus()
    }


    private fun session(s: BluetoothSocket) {
        val input = s.inputStream
        val output = s.outputStream
        BtProtocol.write(output, JSONObject()
            .put("t", "hello").put("v", BtProtocol.VERSION).put("role", "output")
            .put("id", phoneId).put("name", phoneName).put("model", Build.MODEL))

        val welcome = JSONObject(String(BtProtocol.read(input).payload))
        if (welcome.optString("t") != "welcome") return  // not AudioPatch; keep waiting
        _status.update { it.copy(pc = welcome.optString("name", "PC"), waiting = false, error = null) }

        // Per-session helpers stop when this session ends, not just when the mode stops.
        val alive = java.util.concurrent.atomic.AtomicBoolean(true)
        resetPending = true   // don't play leftovers from a previous session
        thread("bt-ping") {
            while (running && alive.get()) {
                try {
                    Thread.sleep(BtProtocol.PING_INTERVAL_MS)
                    BtProtocol.write(output, JSONObject().put("t", "ping"))
                } catch (_: Exception) { return@thread }
            }
        }
        val player = thread("audio-out", Process.THREAD_PRIORITY_URGENT_AUDIO) { playLoop(alive) }

        val decoder = OpusDecoder(BtProtocol.SAMPLE_RATE, BtProtocol.CHANNELS)
        val stats = LinkStats()
        try {
            while (running) {
                val f = BtProtocol.read(input)
                if (f.type == BtProtocol.AUDIO && f.payload.size > 4) {
                    stats.onPacket(BtProtocol.audioSeq(f.payload))
                    decoder.decode(f.payload, 4, f.payload.size - 4) { pcm, frames ->
                        stats.pcmFrames += frames
                        ring.write(pcm, frames)
                    }
                    stats.maybeLog(decoder.inputDrops, ring.overruns, reader.underruns, reader.skips, ring.available,
                        reader.recentMinFrames, reader.lastUnderrunFill, reader.targetFrames)
                } else if (f.type == BtProtocol.CONTROL) {
                    val msg = JSONObject(String(f.payload))
                    if (msg.optString("t") == "state") {
                        val on = msg.optBoolean("streaming")
                        _status.update { it.copy(streaming = on) }
                        if (!on) resetPending = true
                    }
                }
            }
        } finally {
            alive.set(false)
            player.join(1000)   // release the AudioTrack before a new session starts
            decoder.release()
            _status.update { it.copy(streaming = false) }
        }
    }

    private fun playLoop(alive: java.util.concurrent.atomic.AtomicBoolean) {
        val rate = BtProtocol.SAMPLE_RATE
        val minBuf = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_STEREO, AudioFormat.ENCODING_PCM_FLOAT)
        val track = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
            .setAudioFormat(AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
                .setSampleRate(rate)
                .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO).build())
            .setBufferSizeInBytes(max(minBuf, rate / 50 * CH * 4 * 2))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        val n = 240                       // 5 ms per write
        val buf = FloatArray(n * CH)
        var tick = 0
        var peak = 0f
        track.play()
        try {
            while (running && alive.get()) {
                if (resetPending) { reader.reset(); resetPending = false }
                reader.read(buf, n)
                for (v in buf) peak = maxOf(peak, abs(v))
                track.write(buf, 0, buf.size, AudioTrack.WRITE_BLOCKING)   // paces this loop
                if (++tick % 20 == 0) {   // ~10 status updates per second
                    val bufMs = ring.available * 1000 / rate
                    _status.update { it.copy(bufferMs = bufMs, underruns = reader.underruns, level = peak) }
                    peak = 0f
                }
            }
        } finally {
            track.stop()
            track.release()
        }
    }

    /** Link health counters, logged to logcat (tag AudioPatch) every 5 s. */
    private class LinkStats {
        var packets = 0L
        var seqGaps = 0L
        var pcmFrames = 0L
        var maxGapMs = 0L
        private var lastSeq = -1L
        private var lastArrival = 0L
        private var lastLog = SystemClock.elapsedRealtime()

        fun onPacket(seq: Long) {
            val now = SystemClock.elapsedRealtime()
            if (lastArrival != 0L) maxGapMs = maxOf(maxGapMs, now - lastArrival)
            lastArrival = now
            if (lastSeq >= 0 && seq != lastSeq + 1) seqGaps++
            lastSeq = seq
            packets++
        }

        fun maybeLog(decoderDrops: Int, overruns: Int, underruns: Int, skips: Int, buffered: Int, minFill: Int,
                     lastUnderrunFill: Int, target: Int) {
            val now = SystemClock.elapsedRealtime()
            if (now - lastLog < 5000) return
            Log.i("AudioPatch", "link: packets=$packets seqGaps=$seqGaps pcmFrames=$pcmFrames " +
                "(expect ${packets * 960}) decoderDrops=$decoderDrops maxGapMs=$maxGapMs " +
                "overruns=$overruns underruns=$underruns skips=$skips bufferedMs=${buffered / 48} minFillMs=${minFill / 48} lastUnderrunFill=$lastUnderrunFill " +
                "targetMs=${target / 48}")
            lastLog = now
            maxGapMs = 0
        }
    }

    private fun thread(name: String, priority: Int = Process.THREAD_PRIORITY_DEFAULT, body: () -> Unit): Thread {
        val t = Thread({ Process.setThreadPriority(priority); body() }, name)
        t.isDaemon = true
        threads.removeAll { !it.isAlive }
        threads.add(t)
        t.start()
        return t
    }
}
