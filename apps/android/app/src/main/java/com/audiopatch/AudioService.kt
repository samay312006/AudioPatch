package com.audiopatch

import android.app.Service
import android.content.Intent
import android.os.IBinder

/** Foreground service that runs the active mode (filled in per mode). */
class AudioService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null
}
