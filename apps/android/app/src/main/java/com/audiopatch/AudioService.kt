package com.audiopatch

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.ServiceInfo
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.ServiceCompat
import com.audiopatch.modes.PhoneOutput
import com.audiopatch.modes.Receiver
import com.audiopatch.modes.Router

/** Holds the mode engines so the UI and the service share one instance. */
object Engines {
    @Volatile private var receiver: Receiver? = null
    fun receiver(ctx: Context): Receiver =
        receiver ?: synchronized(this) {
            receiver ?: Receiver(Identity.id(ctx), Identity.name(ctx)).also { receiver = it }
        }

    @Volatile private var router: Router? = null
    fun router(ctx: Context): Router =
        router ?: synchronized(this) { router ?: Router(ctx.applicationContext).also { router = it } }
}

/**
 * Foreground service: keeps audio running with the screen off or the app in
 * the background, and shows the "AudioPatch is playing" notification.
 */
class AudioService : Service() {
    companion object {
        const val ACTION_RECEIVE = "com.audiopatch.RECEIVE"
        const val ACTION_STOP = "com.audiopatch.STOP"
        const val ACTION_ROUTE = "com.audiopatch.ROUTE"
        const val ACTION_ROUTE_TONE = "com.audiopatch.ROUTE_TONE"   // debug builds only
        private const val EXTRA_CODE = "code"
        private const val EXTRA_DATA = "data"
        private const val EXTRA_OUTPUTS = "outputs"
        private const val CHANNEL = "playback"
        private const val NOTIFICATION_ID = 1

        fun receive(ctx: Context) = ctx.startForegroundService(
            Intent(ctx, AudioService::class.java).setAction(ACTION_RECEIVE))

        fun stop(ctx: Context) = ctx.startService(Intent(ctx, AudioService::class.java).setAction(ACTION_STOP))

        /** Router with the phone's own audio; [code]/[data] are the screen-capture consent result. */
        fun route(ctx: Context, code: Int, data: Intent, outputs: IntArray) = ctx.startForegroundService(
            Intent(ctx, AudioService::class.java).setAction(ACTION_ROUTE)
                .putExtra(EXTRA_CODE, code).putExtra(EXTRA_DATA, data).putExtra(EXTRA_OUTPUTS, outputs))

        /** Router with a test tone (no consent needed): checks which outputs can play at once. */
        fun routeTone(ctx: Context, outputs: IntArray) = ctx.startForegroundService(
            Intent(ctx, AudioService::class.java).setAction(ACTION_ROUTE_TONE).putExtra(EXTRA_OUTPUTS, outputs))

        /** Turn wireless output back on if it was on when the app last ran. */
        fun resumeIfWasOn(ctx: Context) {
            if (Identity.prefs(ctx).getBoolean("receiver_on", false)) receive(ctx)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_RECEIVE -> {
                Identity.prefs(this).edit().putBoolean("receiver_on", true).apply()
                ServiceCompat.startForeground(this, NOTIFICATION_ID, notification("Wireless output is on"),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
                Engines.receiver(this).start(getSystemService(BluetoothManager::class.java).adapter)
                AppState.set(RunState.RUNNING)
            }
            ACTION_ROUTE -> {
                // The projection must be obtained while already foreground with this type.
                ServiceCompat.startForeground(this, NOTIFICATION_ID, notification("Router is playing your audio"),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
                val data = intent.getParcelableExtra(EXTRA_DATA, Intent::class.java) ?: return START_NOT_STICKY
                val mpm = getSystemService(MediaProjectionManager::class.java)
                val projection = mpm.getMediaProjection(intent.getIntExtra(EXTRA_CODE, 0), data)
                    ?: return START_NOT_STICKY
                projection.registerCallback(object : MediaProjection.Callback() {
                    override fun onStop() { Engines.router(this@AudioService).stop() }
                }, Handler(Looper.getMainLooper()))
                try {
                    Engines.router(this).startCapture(projection, outputsFrom(intent))
                    AppState.set(RunState.RUNNING)
                } catch (e: SecurityException) {
                    AppState.set(RunState.ERROR, error = "Microphone permission is needed to capture audio")
                }
            }
            ACTION_ROUTE_TONE -> {
                if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE == 0) return START_NOT_STICKY
                ServiceCompat.startForeground(this, NOTIFICATION_ID, notification("Router test tone"),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
                Engines.router(this).startTone(outputsFrom(intent))
                AppState.set(RunState.RUNNING)
            }
            ACTION_STOP -> {
                Identity.prefs(this).edit().putBoolean("receiver_on", false).apply()
                Engines.router(this).stop()
                Engines.receiver(this).stop()
                AppState.set(RunState.IDLE)
                ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    private fun outputsFrom(intent: Intent): List<PhoneOutput> {
        val ids = intent.getIntArrayExtra(EXTRA_OUTPUTS) ?: IntArray(0)
        return PhoneOutput.list(this).filter { it.id in ids }
    }

    override fun onDestroy() {
        Engines.router(this).stop()
        Engines.receiver(this).stop()
        super.onDestroy()
    }

    private fun notification(text: String): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Playback", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 1, Intent(this, AudioService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("AudioPatch")
            .setContentText(text)
            .setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "Stop", stop).build())
            .setOngoing(true)
            .build()
    }
}
