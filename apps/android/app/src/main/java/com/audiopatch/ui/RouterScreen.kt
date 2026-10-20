package com.audiopatch.ui

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.audiopatch.AppState
import com.audiopatch.Mode

/**
 * Router is in development. Measured on a real phone: Android plays media to
 * one Bluetooth headset at a time and overrides requests to also use the
 * speaker, so for now this screen points to what works.
 */
@Composable
fun RouterScreen() {
    Card {
        StatusLine(Palette.warn, "In development")
        Spacer(Modifier.height(8.dp))
        Hint("Android plays to only one Bluetooth headset at a time, and apps can't change that " +
            "(only LE Audio earbuds can share through Android's own Audio sharing). We're working on " +
            "other ways to reach more listeners.")
    }
    SectionTitle("Works today")
    Hint("Send this phone's audio to your PC, and AudioPatch on the PC plays it on all its headphones " +
        "and speakers at once, each with its own volume and sync.")
    Spacer(Modifier.height(12.dp))
    Primary("Use Send to PC", { AppState.selectMode(Mode.SENDER) })
}
