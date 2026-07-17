package com.speda.heartbreaker.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.Uplink
import com.speda.heartbreaker.designsystem.background.AmbientBackground
import com.speda.heartbreaker.designsystem.glass.LocalHazeState
import com.speda.heartbreaker.designsystem.glass.hbHazeSource
import com.speda.heartbreaker.designsystem.glass.rememberHbHazeState
import com.speda.heartbreaker.ui.chat.ChatScreen

/**
 * The configured shell root: the ambient void is the single Haze source that all
 * top-level glass blurs over; [ChatScreen] lays out the HUD strip, header,
 * transcript/welcome, composer and the sidebar drawer over it.
 */
@Composable
fun MainScreen(
    graph: AppGraph,
    uplink: Uplink,
    agentId: String,
    partyEngaged: Boolean,
    onAgentChange: (String) -> Unit,
    onPartyToggle: () -> Unit,
    onResetUplink: () -> Unit,
) {
    val haze = rememberHbHazeState()

    Box(Modifier.fillMaxSize()) {
        // ONLY the ambient is the haze source. Marking the whole tree would feed
        // the UI back into its own blur (a surface sampling the text beneath it,
        // and itself); glass refracting the void is both correct and cheap.
        AmbientBackground(Modifier.matchParentSize().hbHazeSource(haze))

        // Every hbGlass surface below picks this up and becomes real frosted
        // glass, without any call site knowing about Haze.
        CompositionLocalProvider(LocalHazeState provides haze) {
            ChatScreen(
                graph = graph,
                uplink = uplink,
                agentId = agentId,
                partyEngaged = partyEngaged,
                onAgentChange = onAgentChange,
                onPartyToggle = onPartyToggle,
                onResetUplink = onResetUplink,
                haze = haze,
                // statusBarsPadding resolves to 0 while the status bar is hidden
                // (fullscreen), and re-applies if it's swiped back in.
                modifier = Modifier.fillMaxSize().statusBarsPadding(),
            )
        }
    }
}
