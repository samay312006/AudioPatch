package com.audiopatch

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import androidx.core.app.ServiceCompat
import com.audiopatch.modes.Receiver

/** Holds the mode engines so the UI and the service share one instance. */
object Engines {
    @Volatile private var receiver: Receiver? = null
    fun receiver(ctx: Context): Receiver =
        receiver ?: synchronized(this) {
            receiver ?: Receiver(Identity.id(ctx), Identity.name(ctx)).also { receiver = it }
        }
}

/**
 * Foreground service: keeps audio running with the screen off or the app in
 * the background, and shows the "AudioPatch is playing" notification.
 */
class AudioService : Service() {
    companion object {
        const val ACTION_RECEIVE = "com.audiopatch.RECEIVE"
        const val ACTION_STOP = "com.audiopatch.STOP"
        private const val CHANNEL = "playback"
        private const val NOTIFICATION_ID = 1

        fun receive(ctx: Context) = ctx.startForegroundService(
            Intent(ctx, AudioService::class.java).setAction(ACTION_RECEIVE))

        fun stop(ctx: Context) = ctx.startService(Intent(ctx, AudioService::class.java).setAction(ACTION_STOP))
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_RECEIVE -> {
                ServiceCompat.startForeground(this, NOTIFICATION_ID, notification("Wireless output is on"),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
                val receiver = Engines.receiver(this)
                receiver.onEnded = { stop(this) }
                receiver.start(getSystemService(BluetoothManager::class.java).adapter)
                AppState.set(RunState.RUNNING)
            }
            ACTION_STOP -> {
                Engines.receiver(this).stop()
                AppState.set(RunState.IDLE)
                ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
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
