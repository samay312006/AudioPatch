package com.audiopatch

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.audiopatch.ui.AudioPatchTheme
import com.audiopatch.ui.Card
import com.audiopatch.ui.Hint
import com.audiopatch.ui.Palette
import com.audiopatch.ui.Primary
import com.audiopatch.ui.ReceiverScreen
import com.audiopatch.ui.SenderScreen
import com.audiopatch.ui.StatusLine

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        debugRouterTone(intent)
        if (savedInstanceState == null && neededPermissions().all {
                ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED }) {
            AudioService.resumeIfWasOn(this)
            if (Identity.prefs(this).getBoolean("receiver_on", false)) AppState.selectMode(Mode.RECEIVER)
        }
        setContent { AudioPatchTheme { App() } }
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        debugRouterTone(intent)
    }

    /** Debug builds: `--es router_tone <output ids>` plays a test tone on those outputs
     *  through the router ("stop" stops it), to test which outputs can play at once. */
    private fun debugRouterTone(intent: android.content.Intent?) {
        if (applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE == 0) return
        intent?.getStringExtra("alarm_tone")?.toIntOrNull()?.let { secs ->
            com.audiopatch.modes.AlarmRouteTest.run(secs)
            return
        }
        val arg = intent?.getStringExtra("router_tone") ?: return
        if (arg == "stop") AudioService.stop(this)
        else AudioService.routeTone(this, arg.split(",").mapNotNull { it.trim().toIntOrNull() }.toIntArray())
    }
}

/** Runtime permissions the Bluetooth modes need (Android 12+ / 13+). */
private fun neededPermissions(): Array<String> = buildList {
    if (Build.VERSION.SDK_INT >= 31) add(Manifest.permission.BLUETOOTH_CONNECT)
    if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
}.toTypedArray()

@Composable
private fun App() {
    val ctx = LocalContext.current
    val ui by AppState.ui.collectAsState()
    val receiver by Engines.receiver(ctx).status.collectAsState()
    val busy = receiver.waiting || receiver.pc != null
    var granted by remember {
        mutableStateOf(neededPermissions().all {
            ContextCompat.checkSelfPermission(ctx, it) == PackageManager.PERMISSION_GRANTED
        })
    }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { res ->
        granted = res.filterKeys { it == Manifest.permission.BLUETOOTH_CONNECT }.values.all { it }
    }

    Column(
        Modifier
            .fillMaxSize()
            .background(Palette.background)
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
    ) {
        Text("AudioPatch", fontSize = 26.sp, fontWeight = FontWeight.Bold, color = Palette.text)
        Spacer(Modifier.height(16.dp))
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            Mode.entries.forEachIndexed { i, mode ->
                SegmentedButton(
                    selected = ui.mode == mode,
                    onClick = { AppState.selectMode(mode) },
                    enabled = !busy || ui.mode == mode,
                    shape = SegmentedButtonDefaults.itemShape(i, Mode.entries.size),
                    label = { Text(mode.title, fontSize = 13.sp, maxLines = 1) },
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        Text(ui.mode.blurb, color = Palette.muted, fontSize = 14.sp)
        Spacer(Modifier.height(20.dp))

        when {
            ui.mode == Mode.ROUTER -> com.audiopatch.ui.RouterScreen()
            !granted -> Card {
                StatusLine(Palette.warn, "Bluetooth access needed")
                Spacer(Modifier.height(8.dp))
                Hint("AudioPatch talks to your PC over Bluetooth, and shows a notification while audio plays.")
                Spacer(Modifier.height(14.dp))
                Primary("Allow", { ask.launch(neededPermissions()) })
            }
            ui.mode == Mode.RECEIVER -> ReceiverScreen()
            ui.mode == Mode.SENDER -> SenderScreen()
        }
    }
}
