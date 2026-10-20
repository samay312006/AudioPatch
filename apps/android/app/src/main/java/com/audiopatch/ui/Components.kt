package com.audiopatch.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.log10

@Composable
fun Card(modifier: Modifier = Modifier, highlight: Boolean = false, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(Palette.card)
            .border(1.dp, if (highlight) Palette.accent.copy(alpha = 0.6f) else Palette.border, RoundedCornerShape(14.dp))
            .padding(16.dp),
        content = content,
    )
}

@Composable
fun SectionTitle(text: String) {
    Text(text.uppercase(), color = Palette.muted, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
        letterSpacing = 1.sp, modifier = Modifier.padding(top = 20.dp, bottom = 8.dp))
}

@Composable
fun Primary(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    Button(onClick, modifier, enabled = enabled,
        colors = ButtonDefaults.buttonColors(containerColor = Palette.accent, contentColor = Palette.background)) {
        Text(text, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
fun Secondary(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    OutlinedButton(onClick, modifier) { Text(text, color = Palette.text) }
}

@Composable
fun StatusDot(color: Color) {
    Box(Modifier.size(8.dp).clip(CircleShape).background(color))
}

@Composable
fun StatusLine(color: Color, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        StatusDot(color)
        Text(text, color = Palette.text, fontSize = 15.sp, modifier = Modifier.padding(start = 8.dp))
    }
}

/** Peak meter on a -60..0 dB scale, like the desktop's. */
@Composable
fun Meter(peak: Float) {
    val db = if (peak > 0f) 20 * log10(peak) else -60f
    val frac = ((db + 60f) / 60f).coerceIn(0f, 1f)
    val color = when { db > -3 -> Palette.bad; db > -12 -> Palette.warn; else -> Palette.good }
    Box(Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(3.dp)).background(Palette.border)) {
        Box(Modifier.fillMaxWidth(frac).height(6.dp).background(color))
    }
}

@Composable
fun Hint(text: String) {
    Text(text, color = Palette.muted, fontSize = 13.sp, lineHeight = 18.sp)
}
