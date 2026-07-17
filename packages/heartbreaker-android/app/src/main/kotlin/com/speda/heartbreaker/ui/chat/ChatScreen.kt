package com.speda.heartbreaker.ui.chat

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.Health
import com.speda.heartbreaker.data.HbSettings
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.ModelInfo
import com.speda.heartbreaker.data.Uplink
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HudStrip
import com.speda.heartbreaker.ui.shell.AppHeader
import com.speda.heartbreaker.ui.shell.SidebarDrawer
import com.speda.heartbreaker.ui.shell.WelcomeView
import kotlinx.coroutines.launch

/**
 * The shell: HUD strip → header → welcome/transcript → composer, with the
 * off-canvas sidebar drawer overlaid (Layout.tsx's slot arrangement at the
 * mobile breakpoint).
 *
 * COMMS and SYS render as header chrome but their surfaces (comms tray, systems
 * board) land in M4; WAR ROOM currently drives the House Party palette parade,
 * which is the takeover's colour behaviour — the full cinematic is M4.
 */
@Composable
fun ChatScreen(
    graph: AppGraph,
    uplink: Uplink,
    agentId: String,
    partyEngaged: Boolean,
    onAgentChange: (String) -> Unit,
    onPartyToggle: () -> Unit,
    onResetUplink: () -> Unit,
    haze: dev.chrisbanes.haze.HazeState,
    modifier: Modifier = Modifier,
) {
    val scope = rememberCoroutineScope()
    val vm: ChatViewModel = viewModel(
        factory = viewModelFactory { initializer { ChatViewModel(graph.api, graph.messageCache) } },
    )
    val config = remember(uplink, agentId) { AppConfig(uplink.apiBase, uplink.apiKey, agentId) }
    LaunchedEffect(config) { vm.onConfig(config) }

    val state by vm.state.collectAsStateWithLifecycle()
    val settings by graph.settings.settings.collectAsStateWithLifecycle(initialValue = HbSettings())
    val health by remember(config) { graph.health.poll(config.apiBase, config.apiKey) }
        .collectAsStateWithLifecycle(initialValue = Health.Offline)

    var models by remember { mutableStateOf<List<ModelInfo>>(emptyList()) }
    LaunchedEffect(config) { models = graph.api.fetchModels(config) }

    var drawerOpen by remember { mutableStateOf(false) }
    val brand = Brands.BRANDS[agentId] ?: Brands.WARROOM
    val activeTitle = state.sessions.firstOrNull { it.id == state.activeSessionId }?.title

    val listState = rememberLazyListState()
    LaunchedEffect(state.messages.size, state.messages.lastOrNull()?.content?.length) {
        if (state.messages.isNotEmpty()) listState.animateScrollToItem(state.messages.lastIndex)
    }

    Box(modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().imePadding()) {
            HudStrip(
                health = health,
                agentName = brand.name,
                model = settings.model,
                sessionCount = state.sessions.size,
                apiBase = config.apiBase,
            )
            AppHeader(
                haze = haze,
                sessionTitle = activeTitle,
                onToggleSidebar = { drawerOpen = true },
            )

            Box(Modifier.weight(1f).fillMaxWidth()) {
                if (state.messages.isEmpty()) {
                    WelcomeView(
                        brand = brand,
                        config = config,
                        api = graph.api,
                        userName = settings.userName,
                    )
                } else {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize().padding(horizontal = 14.dp),
                        contentPadding = PaddingValues(vertical = 12.dp),
                    ) {
                        items(state.messages, key = { it.id }) { message -> MessageItem(message) }
                    }
                }
            }

            Composer(
                isStreaming = state.isStreaming,
                agentName = brand.name,
                models = models,
                model = settings.model,
                onModelChange = { scope.launch { graph.settings.setModel(it) } },
                onSend = { vm.send(it, IgorApi.StreamOpts(model = settings.model.ifEmpty { null })) },
                onStop = { vm.stop() },
                modifier = Modifier.navigationBarsPadding(),
            )
        }

        SidebarDrawer(
            open = drawerOpen,
            brand = brand,
            config = config,
            api = graph.api,
            sessions = state.sessions,
            activeSessionId = state.activeSessionId,
            userName = settings.userName,
            onSelectSession = { vm.selectSession(it) },
            onNewChat = { vm.newChat() },
            onClose = { drawerOpen = false },
            onAgentChange = onAgentChange,
            onResetUplink = onResetUplink,
            // Moved off the header (note #3) — these live in the profile menu now.
            onOpenWarRoom = onPartyToggle,
            onToggleComms = { /* comms tray — M4 */ },
            onToggleBoard = { /* systems board — M4 */ },
        )
    }
}
