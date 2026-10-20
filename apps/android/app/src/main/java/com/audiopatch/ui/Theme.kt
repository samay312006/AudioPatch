package com.audiopatch.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** Same palette as the desktop dashboard (Tailwind zinc + sky). */
object Palette {
    val background = Color(0xFF09090B)   // zinc-950
    val card = Color(0xFF18181B)         // zinc-900
    val border = Color(0xFF3F3F46)       // zinc-700
    val muted = Color(0xFFA1A1AA)        // zinc-400
    val faint = Color(0xFF71717A)        // zinc-500
    val text = Color(0xFFF4F4F5)         // zinc-100
    val accent = Color(0xFF38BDF8)       // sky-400
    val violet = Color(0xFFA78BFA)       // violet-400
    val good = Color(0xFF34D399)         // emerald-400
    val warn = Color(0xFFFBBF24)         // amber-400
    val bad = Color(0xFFFB7185)          // rose-400
}

@Composable
fun AudioPatchTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Palette.accent,
            onPrimary = Palette.background,
            secondary = Palette.violet,
            background = Palette.background,
            onBackground = Palette.text,
            surface = Palette.card,
            onSurface = Palette.text,
            surfaceVariant = Palette.card,
            onSurfaceVariant = Palette.muted,
            outline = Palette.border,
            error = Palette.bad,
        ),
        content = content,
    )
}
