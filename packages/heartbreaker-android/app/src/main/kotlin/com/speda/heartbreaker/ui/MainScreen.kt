package com.speda.heartbreaker.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.Uplink
import com.speda.heartbreaker.designsystem.background.AmbientBackground
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

    Box(Modifier.fillMaxSize().hbHazeSource(haze)) {
        AmbientBackground(Modifier.matchParentSize())

        ChatScreen(
            graph = graph,
            uplink = uplink,
            agentId = agentId,
            partyEngaged = partyEngaged,
            onAgentChange = onAgentChange,
            onPartyToggle = onPartyToggle,
            onResetUplink = onResetUplink,
            haze = haze,
            modifier = Modifier.fillMaxSize().statusBarsPadding(),
        )
    }
}
