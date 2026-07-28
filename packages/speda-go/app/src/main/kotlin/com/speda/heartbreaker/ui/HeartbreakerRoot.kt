package com.speda.heartbreaker.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.UplinkState
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.theme.HbTheme
import com.speda.heartbreaker.designsystem.theme.ThemeEngine
import kotlinx.coroutines.launch

/**
 * The app root: owns the live agent accent + party flag (so the whole tree morphs
 * from one place, like App.tsx) and routes first-run vs configured.
 *
 * M0 runs a single agent, but the token gallery can switch the accent to exercise
 * the morph + ambient re-hue and can engage the House Party parade — the M0
 * "theme engine + morph + party cycle" acceptance surface.
 */
@Composable
fun HeartbreakerRoot(graph: AppGraph) {
    val scope = rememberCoroutineScope()
    val uplinkState by graph.uplink.state.collectAsStateWithLifecycle(initialValue = null)

    var agentId by rememberSaveable { mutableStateOf(Brands.DEFAULT_AGENT) }
    var partyEngaged by rememberSaveable { mutableStateOf(false) }
    val accent = Brands.BRANDS[agentId]?.accent ?: Brands.WARROOM.accent

    HbTheme(accentHex = accent, partyEngaged = partyEngaged) {
        val void = ThemeEngine.buildPalette(accent).void
        Box(Modifier.fillMaxSize().background(void)) {
            when (val s = uplinkState) {
                null -> Unit // brief DataStore read; the void shows
                UplinkState.Unconfigured -> UplinkSetupScreen(
                    onConnect = { base, key -> scope.launch { graph.uplink.save(base, key) } },
                )
                is UplinkState.Configured -> MainScreen(
                    graph = graph,
                    uplink = s.uplink,
                    agentId = agentId,
                    partyEngaged = partyEngaged,
                    onAgentChange = { agentId = it },
                    onPartyToggle = { partyEngaged = !partyEngaged },
                    onResetUplink = { scope.launch { graph.uplink.clear() } },
                )
            }
        }
    }
}
