package com.speda.heartbreaker.ui.chat

import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.runtime.CompositionLocalProvider
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
import com.speda.heartbreaker.designsystem.glass.LocalHazeState
import com.speda.heartbreaker.designsystem.glass.hbHazeSource
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HudStrip
import com.speda.heartbreaker.ui.comms.AgentCommsScreen
import com.speda.heartbreaker.ui.settings.SettingsScreen
import com.speda.heartbreaker.ui.systems.SystemsBoardScreen
import com.speda.heartbreaker.ui.switcher.AgentSwitcherOverlay
import com.speda.heartbreaker.ui.shell.AppHeader
import com.speda.heartbreaker.ui.shell.SidebarDrawer
import com.speda.heartbreaker.ui.shell.WelcomeView
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * The shell: HUD strip → header → welcome/transcript → composer, with the
 * off-canvas sidebar drawer overlaid (Layout.tsx's slot arrangement at the
 * mobile breakpoint).
 *
 * COMMS and SYS open the agent comms tray and systems board (M4 surfaces).
 * WAR ROOM currently drives the House Party palette parade, which is the
 * takeover's colour behaviour — the full cinematic is still M4 work.
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

    // Ambient client context (platform + opt-in location) resolved fresh per turn.
    val locationPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        scope.launch {
            graph.settings.setLocationPrompted(true)
            if (granted) graph.settings.setLocationEnabled(true)
        }
    }
    LaunchedEffect(Unit) {
        vm.clientContextProvider = {
            graph.platform.snapshot(includeLocation = graph.settings.settings.first().locationEnabled)
        }
        // Prompt for location once on first launch (the owner's chosen default).
        val s = graph.settings.settings.first()
        if (!s.locationPrompted) {
            if (graph.platform.hasLocationPermission()) {
                graph.settings.setLocationPrompted(true); graph.settings.setLocationEnabled(true)
            } else {
                locationPermission.launch(android.Manifest.permission.ACCESS_FINE_LOCATION)
            }
        }
    }

    var drawerOpen by remember { mutableStateOf(false) }
    var settingsOpen by remember { mutableStateOf(false) }
    var commsOpen by remember { mutableStateOf(false) }
    var boardOpen by remember { mutableStateOf(false) }
    var switcherOpen by remember { mutableStateOf(false) }
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
                onAgentClick = { switcherOpen = true },
            )
            AppHeader(
                haze = haze,
                sessionTitle = activeTitle,
                onToggleSidebar = { drawerOpen = true },
            )

            // The transcript is ALSO a backdrop: the header, composer and drawer
            // sit over it, so they must refract the text behind them — that's
            // what makes them read as glass rather than tinted panels. (Blurring
            // only the ambient shows nothing: a smooth gradient blurs to itself.)
            Box(Modifier.weight(1f).fillMaxWidth().hbHazeSource(haze)) {
                // Glass INSIDE the backdrop must not blur — it would sample the
                // source it lives in, itself included. It falls back to the
                // occluding fill, which is exactly the CSS's nested-backdrop-root
                // rule: a nested surface's own blur is cancelled anyway.
                CompositionLocalProvider(LocalHazeState provides null) {
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
                            items(state.messages, key = { it.id }) { message ->
                                MessageItem(
                                    message,
                                    config = config,
                                    downloader = graph.downloader,
                                    onDelete = { vm.deleteMessage(message.id) },
                                    onRegenerate = if (message.role == com.speda.heartbreaker.domain.Role.Assistant) {
                                        { vm.regenerate(message.id, turnOpts(settings)) }
                                    } else null,
                                    onEditAndResend = if (message.role == com.speda.heartbreaker.domain.Role.User) {
                                        { newText -> vm.editAndResend(message.id, newText, turnOpts(settings)) }
                                    } else null,
                                )
                            }
                        }
                    }
                }
            }

            Composer(
                isStreaming = state.isStreaming,
                agentName = brand.name,
                models = models,
                model = settings.model,
                onModelChange = { scope.launch { graph.settings.setModel(it) } },
                onSend = { text, images, docs ->
                    vm.send(
                        text,
                        IgorApi.StreamOpts(
                            model = settings.model.ifEmpty { null },
                            systemPrompt = settings.systemPrompt.ifBlank { null },
                            images = images,
                            documents = docs,
                        ),
                    )
                },
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
            onRenameSession = { id, title -> vm.renameSession(id, title) },
            onDeleteSession = { vm.deleteSession(it) },
            onClose = { drawerOpen = false },
            onAgentChange = onAgentChange,
            onResetUplink = onResetUplink,
            onOpenSettings = { drawerOpen = false; settingsOpen = true },
            // Moved off the header (note #3) — these live in the profile menu now.
            onOpenWarRoom = onPartyToggle,
            onToggleComms = { drawerOpen = false; commsOpen = true },
            onToggleBoard = { drawerOpen = false; boardOpen = true },
        )

        // Full-screen settings sheet over everything; back closes it.
        if (settingsOpen) {
            BackHandler { settingsOpen = false }
            SettingsScreen(
                config = config,
                graph = graph,
                settings = settings,
                models = models,
                brand = brand,
                onResetUplink = { settingsOpen = false; onResetUplink() },
                onClose = { settingsOpen = false },
            )
        }

        // AGENT_COMMS — the inter-agent traffic tray, a full-width bottom slab.
        if (commsOpen) {
            AgentCommsScreen(config = config, api = graph.api, onClose = { commsOpen = false })
        }

        // SYSTEMS 56A. — uplink, routing matrix, budget, RTT trace, knowledge bank.
        if (boardOpen) {
            BackHandler { boardOpen = false }
            SystemsBoardScreen(
                config = config,
                graph = graph,
                settings = settings,
                sessionCount = state.sessions.size,
                onClose = { boardOpen = false },
            )
        }

        // The armoury — cinematic agent switcher, opened from the HUD agent name.
        if (switcherOpen) {
            BackHandler { switcherOpen = false }
            AgentSwitcherOverlay(
                currentAgentId = agentId,
                onSelect = { switcherOpen = false; onAgentChange(it) },
                onClose = { switcherOpen = false },
            )
        }
    }
}

/**
 * The model/prompt options a fresh turn carries — mirrors the composer's onSend,
 * minus attachments (regenerate/edit-and-resend re-run the text turn only).
 */
private fun turnOpts(settings: HbSettings) = IgorApi.StreamOpts(
    model = settings.model.ifEmpty { null },
    systemPrompt = settings.systemPrompt.ifBlank { null },
)
