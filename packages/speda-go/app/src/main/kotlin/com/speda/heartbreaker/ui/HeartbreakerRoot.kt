package com.speda.heartbreaker.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
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
import com.speda.heartbreaker.ui.party.ActivationMode
import com.speda.heartbreaker.ui.party.PartyActivation
import com.speda.heartbreaker.domain.AppConfig
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.launch

/**
 * The app root: owns the live agent accent and the WAR ROOM state (so the whole
 * tree morphs from one place, like App.tsx), and routes first-run vs configured.
 */

/**
 * Three states, not a boolean — the desktop's `warMode`.
 *
 *   Off      a normal agent.
 *   Standby  the owner opened the war room from the UI. Full branded takeover
 *            (roster colour parade, warroom profile) but the protocol is NOT
 *            engaged: nothing is dispatched and nothing is being paid for.
 *   Engaged  the owner authorized the protocol (backend flag). Same takeover,
 *            plus live full-roster dispatch and STAND DOWN.
 *
 * Collapsing standby and engaged into one flag is what makes an expensive
 * protocol look identical to a colour scheme.
 */
enum class WarMode { Off, Standby, Engaged }

@Composable
fun HeartbreakerRoot(graph: AppGraph) {
    val scope = rememberCoroutineScope()
    val uplinkState by graph.uplink.state.collectAsStateWithLifecycle(initialValue = null)

    var agentId by rememberSaveable { mutableStateOf(Brands.DEFAULT_AGENT) }
    var warMode by rememberSaveable { mutableStateOf(WarMode.Off) }
    /** Where EXIT / STAND DOWN returns to — the last agent before the takeover. */
    var prevAgent by rememberSaveable { mutableStateOf(Brands.DEFAULT_AGENT) }

    val accent = Brands.BRANDS[agentId]?.accent ?: Brands.WARROOM.accent
    val configured = uplinkState as? UplinkState.Configured

    // ── The cinematic ─────────────────────────────────────────────────────────
    // Every transition in and out of the war room is played, never snapped. The
    // world swaps at the sequence's ignite point, while the screen is fully
    // covered — see PartyActivation. `pending` is where an in-flight ENTER is
    // heading, since standby and engaged share one entrance.
    var activation by remember { mutableStateOf<ActivationMode?>(null) }
    var pending by remember { mutableStateOf(WarMode.Standby) }

    val enterWarRoom = {
        if (warMode == WarMode.Off && activation == null) {
            pending = WarMode.Standby
            activation = ActivationMode.Standby
        }
    }

    val engageParty = {
        if (activation == null) {
            pending = WarMode.Engaged
            // Already staged: the room is up, only the protocol's status
            // changes, so escalate in place rather than replaying the entrance.
            if (warMode == WarMode.Standby) warMode = WarMode.Engaged
            else activation = ActivationMode.Engage
        }
    }

    /**
     * `userInitiated` also drops the backend flag. A poll-driven exit must not
     * write back the state it just read — that would race the desktop turning
     * the protocol on.
     */
    val exitWarRoom = { userInitiated: Boolean ->
        if (warMode != WarMode.Off && activation == null) {
            val cfg = configured?.uplink
            if (userInitiated && warMode == WarMode.Engaged && cfg != null) {
                scope.launch {
                    graph.api.setHouseParty(AppConfig(cfg.apiBase, cfg.apiKey, agentId), engaged = false)
                }
            }
            activation = ActivationMode.StandDown
        }
    }

    // ── The backend owns the truth ────────────────────────────────────────────
    // The protocol can be engaged by TELLING SPEDA — from this phone, the
    // desktop, or Telegram — so the flag is polled rather than assumed.
    // Collected with collectAsStateWithLifecycle, so the poll stops when the app
    // is backgrounded and takes a fresh reading the moment it comes back.
    val partyFlow = remember(configured, agentId) {
        flow {
            val cfg = configured?.uplink ?: return@flow
            val config = AppConfig(cfg.apiBase, cfg.apiKey, agentId)
            while (true) {
                emit(graph.api.getHouseParty(config))
                delay(4_000)
            }
        }
    }
    // Nullable on purpose: `false` is a real reading and must not be manufactured
    // as an initial value, or a resume would stand the protocol down before the
    // first poll ever answered.
    val backendEngaged by partyFlow.collectAsStateWithLifecycle(initialValue = null)
    LaunchedEffect(backendEngaged, activation) {
        // Never interrupt a running cinematic — it is mid-swap, and the reading
        // that arrived belongs to the world on one side of it or the other.
        if (activation != null) return@LaunchedEffect
        when {
            backendEngaged == true && warMode != WarMode.Engaged -> engageParty()
            backendEngaged == false && warMode == WarMode.Engaged -> exitWarRoom(false)
        }
    }

    // Both war-room states parade the palette: the room is never a flat single
    // hue while the roster is staged.
    HbTheme(accentHex = accent, partyEngaged = warMode != WarMode.Off) {
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
                    warMode = warMode,
                    onAgentChange = { next ->
                        // Picking a real agent from the switcher while the war
                        // room is up leaves it — the same rule as the desktop.
                        if (warMode != WarMode.Off) { prevAgent = next; exitWarRoom(true) } else agentId = next
                    },
                    onEnterWarRoom = enterWarRoom,
                    onExitWarRoom = { exitWarRoom(true) },
                    // The modal just got a yes from the backend — don't wait for
                    // the 4s poll to catch up to what we already know.
                    onPartyEngaged = engageParty,
                    onResetUplink = { scope.launch { graph.uplink.clear() } },
                )
            }

            // Over everything, including the overlays: the transformation is the
            // subject, and anything drawn on top of it would be watching itself
            // change clothes.
            activation?.let { mode ->
                PartyActivation(
                    mode = mode,
                    onIgnite = {
                        // Fully covered — swap the world.
                        if (mode == ActivationMode.StandDown) {
                            agentId = prevAgent
                            warMode = WarMode.Off
                        } else {
                            if (agentId != Brands.WARROOM.agentId) prevAgent = agentId
                            agentId = Brands.WARROOM.agentId
                            warMode = pending
                        }
                    },
                    onDone = { activation = null },
                )
            }
        }
    }
}
