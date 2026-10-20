package com.audiopatch.ui

import android.annotation.SuppressLint
import android.bluetooth.BluetoothClass
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.Intent
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.provider.Settings
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.audiopatch.AudioService
import com.audiopatch.Engines
import com.audiopatch.Identity

data class PairedDevice(val name: String, val address: String, val isComputer: Boolean)

@SuppressLint("MissingPermission")  // only called once BLUETOOTH_CONNECT is granted
fun pairedDevices(ctx: Context): List<PairedDevice> {
    val adapter = ctx.getSystemService(BluetoothManager::class.java)?.adapter ?: return emptyList()
    return adapter.bondedDevices.orEmpty().map { d: BluetoothDevice ->
        PairedDevice(d.name ?: d.address, d.address,
            d.bluetoothClass?.majorDeviceClass == BluetoothClass.Device.Major.COMPUTER)
    }.sortedWith(compareByDescending<PairedDevice> { it.isComputer }.thenBy { it.name })
}

fun bluetoothOn(ctx: Context): Boolean =
    ctx.getSystemService(BluetoothManager::class.java)?.adapter?.isEnabled == true

fun openBluetoothSettings(ctx: Context) =
    ctx.startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))

// ------------------------------------------------------------- Wireless output

@Composable
fun ReceiverScreen() {
    val ctx = LocalContext.current
    val status by Engines.receiver(ctx).status.collectAsState()
    val on = status.waiting || status.pc != null
    val me = Identity.name(ctx)

    if (!bluetoothOn(ctx)) {
        Card {
            StatusLine(Palette.warn, "Bluetooth is off")
            Spacer(Modifier.height(12.dp))
            Primary("Open Bluetooth settings", { openBluetoothSettings(ctx) })
        }
        return
    }

    if (!on) {
        status.error?.let {
            Card { StatusLine(Palette.bad, "Stopped"); Spacer(Modifier.height(6.dp)); Hint(it) }
            Spacer(Modifier.height(12.dp))
        }
        Primary("Turn on wireless output", { AudioService.receive(ctx) }, Modifier.fillMaxWidth())
        SectionTitle("How it works")
        Step(1, "Pair this phone with your PC in Bluetooth settings (once).")
        Step(2, "Turn on wireless output here.")
        Step(3, "On the PC, open AudioPatch → Phones → Use as output next to \"$me\", then + Add it under Outputs.")
        Spacer(Modifier.height(4.dp))
        Hint("It plays on whatever this phone is using: its speaker, or headphones connected to it.")
        return
    }

    Card(highlight = status.streaming) {
        when {
            status.pc == null -> StatusLine(Palette.warn, "Waiting for your PC…")
            status.streaming -> StatusLine(Palette.good, "Playing audio from ${status.pc}")
            else -> StatusLine(Palette.accent, "Connected to ${status.pc}")
        }
        Spacer(Modifier.height(10.dp))
        when {
            status.pc == null -> Hint("On the PC, open AudioPatch → Phones → Use as output next to \"$me\".")
            !status.streaming -> Hint("On the PC, click + Add on \"$me\" under Outputs. Audio starts as soon as you do.")
            else -> {
                Meter(status.level)
                Spacer(Modifier.height(8.dp))
                Hint("Buffer ${status.bufferMs} ms" + if (status.underruns > 0) " · ${status.underruns} dropouts" else "")
            }
        }
        Spacer(Modifier.height(16.dp))
        Secondary("Turn off", { AudioService.stop(ctx) }, Modifier.fillMaxWidth())
    }
    Spacer(Modifier.height(12.dp))
    Hint("Use Calibrate sync on the PC to line this phone up with the PC's other devices.")
}

// ------------------------------------------------------------------ Send to PC

/** Name of the PC this phone's audio is going to right now, if any. */
@Composable
private fun rememberAudioToPc(ctx: Context): String? {
    val am = remember { ctx.getSystemService(AudioManager::class.java) }
    var current by remember { mutableStateOf<String?>(null) }
    DisposableEffect(Unit) {
        fun refresh() {
            val pcNames = pairedDevices(ctx).filter { it.isComputer }.map { it.name }.toSet()
            current = am.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
                .firstOrNull { it.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP && it.productName.toString() in pcNames }
                ?.productName?.toString()
        }
        val cb = object : AudioDeviceCallback() {
            override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>?) = refresh()
            override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>?) = refresh()
        }
        refresh()
        am.registerAudioDeviceCallback(cb, null)
        onDispose { am.unregisterAudioDeviceCallback(cb) }
    }
    return current
}

@Composable
fun SenderScreen() {
    val ctx = LocalContext.current
    val pc = rememberAudioToPc(ctx)
    Card(highlight = pc != null) {
        if (pc != null) {
            StatusLine(Palette.good, "This phone's audio is playing on $pc")
            Spacer(Modifier.height(8.dp))
            Hint("Everything you play here (YouTube, Spotify, Netflix, games…) goes to the PC, " +
                "and AudioPatch sends it to the PC's outputs.")
        } else {
            StatusLine(Palette.faint, "Not connected to a PC")
        }
    }
    SectionTitle("How it works")
    Hint("Your PC becomes a Bluetooth speaker for this phone. It works with every app, " +
        "and needs nothing running on this phone.")
    Spacer(Modifier.height(12.dp))
    Step(1, "Pair this phone with your PC in Bluetooth settings (once).")
    Step(2, "On the PC, open AudioPatch → Phone audio → Connect next to \"${Identity.name(ctx)}\".")
    Step(3, "Play anything. Make sure System audio is a source on the PC.")
    Spacer(Modifier.height(16.dp))
    Secondary("Bluetooth settings", { openBluetoothSettings(ctx) })
}

@Composable
private fun Step(n: Int, text: String) {
    Row(Modifier.padding(bottom = 10.dp)) {
        Text("$n", color = Palette.accent, fontWeight = FontWeight.Bold, modifier = Modifier.padding(end = 12.dp))
        Text(text, color = Palette.text, fontSize = 14.sp, lineHeight = 20.sp)
    }
}
