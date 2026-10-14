// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.Uplink
import com.speda.heartbreaker.designsystem.background.AmbientBackground
import com.speda.heartbreaker.ui.chat.ChatScreen
import com.speda.heartbreaker.ui.prose.LocalAircraftResolver
import com.speda.heartbreaker.ui.prose.LocalBoardImageResolver
import com.speda.heartbreaker.ui.prose.LocalPlaceResolver
import com.speda.heartbreaker.ui.prose.LocalRouteResolver
import com.speda.heartbreaker.domain.AircraftSpec
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.MapPlace
import com.speda.heartbreaker.domain.RouteGeometry

/**
 * The configured shell root: a static void beneath cached glass fills.
 * [ChatScreen] lays out the HUD strip, header,
 * transcript/welcome, composer and the sidebar drawer over it.
 */
@Composable
fun MainScreen(
    graph: AppGraph,
    uplink: Uplink,
    agentId: String,
    onAgentChange: (String) -> Unit,
    onResetUplink: () -> Unit,
) {
    Box(Modifier.fillMaxSize()) {
        AmbientBackground(Modifier.matchParentSize())
        // Map cards reference route geometry by id and resolve it here, against
        // the same Igor the chat streams from. Provided once at the root so no
        // prose block needs to know about AppGraph.
        val routeConfig = remember(uplink, agentId) {
            AppConfig(uplink.apiBase, uplink.apiKey, agentId)
        }
        val resolveRoute = remember(routeConfig) {
            // Declared explicitly rather than inferred: a lambda literal only
            // converts to a suspend type when the expected type is unambiguous,
            // and through remember's type parameter it is not.
            val fn: suspend (String) -> RouteGeometry? = { routeId ->
                graph.api.fetchRouteGeometry(routeConfig, routeId)
            }
            fn
        }
        // Place searches follow the same rule as routes: the fence carries one
        // id and the records are fetched here.
        val resolvePlaces = remember(routeConfig) {
            val fn: suspend (String) -> List<MapPlace>? = { placesId ->
                graph.api.fetchPlaceSet(routeConfig, placesId)
            }
            fn
        }
        // Live ADS-B position by tail number — polled repeatedly by the card
        // itself, not resolved once like routes/places (see
        // app/services/aircraft.py for why this one has no id-store).
        val resolveAircraft = remember(routeConfig) {
            val fn: suspend (String) -> AircraftSpec? = { tail ->
                graph.api.fetchAircraftTrack(routeConfig, tail)
            }
            fn
        }

        // Board pictures — see LocalBoardImageResolver for why the phone never
        // fetches one from its own origin.
        val resolveBoardImage = remember(routeConfig) {
            val fn: suspend (String) -> ByteArray? = { url ->
                graph.api.fetchBoardImage(routeConfig, url)
            }
            fn
        }

        CompositionLocalProvider(
            LocalBoardImageResolver provides resolveBoardImage,
            LocalRouteResolver provides resolveRoute,
            LocalPlaceResolver provides resolvePlaces,
            LocalAircraftResolver provides resolveAircraft,
        ) {
            ChatScreen(
                graph = graph,
                uplink = uplink,
                agentId = agentId,
                onAgentChange = onAgentChange,
                onResetUplink = onResetUplink,
                // statusBarsPadding resolves to 0 while the status bar is hidden
                // (fullscreen), and re-applies if it's swiped back in.
                modifier = Modifier.fillMaxSize().statusBarsPadding(),
            )
        }
    }
}
