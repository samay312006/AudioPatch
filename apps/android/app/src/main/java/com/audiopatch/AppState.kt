package com.audiopatch

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

enum class Mode(val title: String, val blurb: String) {
    ROUTER("Router", "Play this phone's audio on several of its outputs at once"),
    RECEIVER("Wireless output", "Play your PC's audio on this phone"),
    SENDER("Send to PC", "Play this phone's audio through your PC"),
}

/** What the running mode is doing, shown in the UI and the notification. */
enum class RunState { IDLE, STARTING, WAITING, RUNNING, ERROR }

data class UiState(
    val mode: Mode = Mode.RECEIVER,
    val run: RunState = RunState.IDLE,
    val status: String = "",
    val error: String? = null,
)

/**
 * Single source of truth shared by the UI and AudioService. Kept deliberately
 * small: each mode keeps its detailed state in its own StateFlow.
 */
object AppState {
    private val _ui = MutableStateFlow(UiState())
    val ui: StateFlow<UiState> = _ui.asStateFlow()

    fun selectMode(mode: Mode) = _ui.update { if (it.run == RunState.IDLE || it.run == RunState.ERROR) it.copy(mode = mode, error = null) else it }
    fun set(run: RunState, status: String = "", error: String? = null) =
        _ui.update { it.copy(run = run, status = status, error = error) }
}
