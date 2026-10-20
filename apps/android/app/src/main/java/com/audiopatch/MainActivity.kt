package com.audiopatch

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.audiopatch.ui.AudioPatchTheme
import com.audiopatch.ui.Palette

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { AudioPatchTheme { App() } }
    }
}

@Composable
private fun App() {
    val ui by AppState.ui.collectAsState()
    val busy = ui.run != RunState.IDLE && ui.run != RunState.ERROR
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
        ModePlaceholder(ui.mode)
    }
}

@Composable
private fun ModePlaceholder(mode: Mode) {
    Row(horizontalArrangement = Arrangement.Center, modifier = Modifier.fillMaxWidth()) {
        Text("${mode.title} coming up next", color = Palette.faint)
    }
}
