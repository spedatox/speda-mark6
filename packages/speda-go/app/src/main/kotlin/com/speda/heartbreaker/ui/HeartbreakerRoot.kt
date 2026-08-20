package com.speda.heartbreaker.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.HbSettings
import com.speda.heartbreaker.data.UplinkState
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.theme.HbTheme
import com.speda.heartbreaker.designsystem.theme.ThemeEngine
import com.speda.heartbreaker.i18n.AppLocale
import com.speda.heartbreaker.i18n.En
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.i18n.Tr
import kotlinx.coroutines.launch

/**
 * The app root: owns the live agent accent (so the whole tree morphs from one
 * place, like App.tsx) and routes first-run vs configured.
 */

/**
 * THE HOUSE PARTY PROTOCOL IS NOT HERE, ON PURPOSE.
 *
 * The war room is a desktop surface: it stages the whole roster, runs a live
 * transcript beside it and parades the palette across the entire deck. A phone
 * has nowhere to put that, and a protocol that runs every agent at full
 * interactive grade while showing the owner nothing is worse than one that
 * simply is not offered. The backend agrees — `POST /agents/house-party` and
 * the `house_party` tool both refuse to ENGAGE from a non-desktop surface, and
 * Speda says so in its own words when asked from the phone.
 *
 * Standing down deliberately stays possible from anywhere: the owner can tell
 * Speda to stand the protocol down from this app, and the tool will. A protocol
 * you can start but not stop is a trap.
 */
@Composable
fun HeartbreakerRoot(graph: AppGraph) {
    val scope = rememberCoroutineScope()
    val uplinkState by graph.uplink.state.collectAsStateWithLifecycle(initialValue = null)
    val settings by graph.settings.settings.collectAsStateWithLifecycle(initialValue = HbSettings())

    var agentId by rememberSaveable { mutableStateOf(Brands.DEFAULT_AGENT) }

    val accent = Brands.BRANDS[agentId]?.accent ?: Brands.BRANDS.getValue(Brands.DEFAULT_AGENT).accent

    // The interface language — Turkish by default (store/settings.ts's `locale`,
    // mirrored). Provided once here so every screen, including the pre-login
    // UplinkSetupScreen, reads from the same dictionary.
    val strings = if (AppLocale.entries.firstOrNull { it.wire == settings.locale } == AppLocale.EN) En else Tr

    CompositionLocalProvider(LocalStrings provides strings) {
        HbTheme(accentHex = accent, partyEngaged = false) {
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
                        onAgentChange = { next -> agentId = next },
                        onResetUplink = { scope.launch { graph.uplink.clear() } },
                    )
                }
            }
        }
    }
}
