package com.audiopatch

import android.content.Context
import android.os.Build
import android.provider.Settings
import java.util.UUID

/** How this phone introduces itself to the PC. The id survives app restarts so
 *  the PC remembers its volume and delay settings. */
object Identity {
    fun id(ctx: Context): String {
        val prefs = ctx.getSharedPreferences("audiopatch", Context.MODE_PRIVATE)
        return prefs.getString("phone_id", null) ?: UUID.randomUUID().toString().also {
            prefs.edit().putString("phone_id", it).apply()
        }
    }

    fun name(ctx: Context): String =
        Settings.Global.getString(ctx.contentResolver, Settings.Global.DEVICE_NAME)
            ?.takeIf { it.isNotBlank() } ?: "${Build.MANUFACTURER} ${Build.MODEL}"
}
