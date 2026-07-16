package com.speda.heartbreaker.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.Health
import com.speda.heartbreaker.data.Uplink
import com.speda.heartbreaker.designsystem.background.AmbientBackground
import com.speda.heartbreaker.designsystem.glass.hbHazeSource
import com.speda.heartbreaker.designsystem.glass.rememberHbHazeState
import com.speda.heartbreaker.ui.gallery.TokenGalleryScreen

/**
 * The configured shell: ambient void (the single Haze source) → HUD strip → body.
 * For M0 the body is the token gallery, the visual-parity reference surface.
 * M1+ replaces it with the real chat/welcome surface.
 *
 * Health polling is collected via collectAsStateWithLifecycle, so it pauses in
 * the background and resumes with an immediate tick (plan §4.2 lifecycle rule).
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
    val health by remember(uplink) { graph.health.poll(uplink.apiBase, uplink.apiKey) }
        .collectAsStateWithLifecycle(initialValue = Health.Offline)

    Box(Modifier.fillMaxSize().hbHazeSource(haze)) {
        AmbientBackground(Modifier.matchParentSize())

        Column(Modifier.fillMaxSize().statusBarsPadding()) {
            HudStrip(health)
            TokenGalleryScreen(
                modifier = Modifier.fillMaxSize(),
                haze = haze,
                agentId = agentId,
                partyEngaged = partyEngaged,
                onAgentChange = onAgentChange,
                onPartyToggle = onPartyToggle,
                onResetUplink = onResetUplink,
            )
        }
    }
}
