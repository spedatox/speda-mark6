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
import androidx.compose.runtime.derivedStateOf
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
import com.speda.heartbreaker.designsystem.glass.LocalAmbientHazeState
import com.speda.heartbreaker.designsystem.glass.LocalHazeState
import com.speda.heartbreaker.designsystem.glass.hbHazeSource
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HudStrip
import com.speda.heartbreaker.ui.comms.AgentCommsScreen
import com.speda.heartbreaker.data.SkyfallArm
import com.speda.heartbreaker.ui.skyfall.SkyfallCountdown
import com.speda.heartbreaker.ui.settings.SettingsScreen
import com.speda.heartbreaker.ui.systems.SystemsBoardScreen
import com.speda.heartbreaker.ui.switcher.AgentSwitcherOverlay
import com.speda.heartbreaker.ui.shell.AppHeader
import com.speda.heartbreaker.ui.shell.SidebarDrawer
import com.speda.heartbreaker.ui.shell.WelcomeView
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/** Scroll offset far past any real message height, so LazyList clamps to the
 *  very bottom of the target item — "show me the END of this message", not its
 *  first line. Deliberately not Int.MAX_VALUE: the offset is summed with layout
 *  positions internally, and a bounded value can't overflow that arithmetic. */
private const val SCROLL_TO_END = 1_000_000

/** How far off the bottom (px) still counts as "parked at the bottom", so a
 *  pixel of overscroll or a settling layout doesn't stop the stream following. */
private const val STICK_SLACK_PX = 96

/**
 * The shell: HUD strip → header → welcome/transcript → composer, with the
 * off-canvas sidebar drawer overlaid (Layout.tsx's slot arrangement at the
 * mobile breakpoint).
 *
 * COMMS and SYS open the agent comms tray and systems board (M4 surfaces).
 *
 * There is no war room here. The House Party Protocol is a desktop surface —
 * see the note on HeartbreakerRoot.
 */
@Composable
fun ChatScreen(
    graph: AppGraph,
    uplink: Uplink,
    agentId: String,
    onAgentChange: (String) -> Unit,
    onResetUplink: () -> Unit,
    haze: dev.chrisbanes.haze.HazeState,
    modifier: Modifier = Modifier,
) {
    val scope = rememberCoroutineScope()
    val vm: ChatViewModel = viewModel(
        factory = viewModelFactory { initializer { ChatViewModel(graph.api, graph.messageCache) } },
    )
    // ChatViewModel is plain Kotlin, not @Composable — it can't read LocalStrings
    // itself, so the shell keeps it in sync on every recomposition.
    vm.strings = LocalStrings.current
    val config = remember(uplink, agentId) { AppConfig(uplink.apiBase, uplink.apiKey, agentId) }
    LaunchedEffect(config) { vm.onConfig(config) }

    val state by vm.state.collectAsStateWithLifecycle()
    val settings by graph.settings.settings.collectAsStateWithLifecycle(initialValue = HbSettings())
    val health by remember(config) { graph.health.poll(config.apiBase, config.apiKey) }
        .collectAsStateWithLifecycle(initialValue = Health.Offline)

    var models by remember { mutableStateOf<List<ModelInfo>>(emptyList()) }
    LaunchedEffect(config) { models = graph.api.fetchModels(config) }

    // Budget mode is SHARED state, not a client preference: Speda can flip it
    // itself mid-turn, so the client reads it on entry and re-syncs after every
    // turn settles rather than trusting its own last write.
    var budgetMode by remember { mutableStateOf<Boolean?>(null) }
    LaunchedEffect(config) { budgetMode = graph.api.getBudgetMode(config) }
    LaunchedEffect(config, state.isStreaming) {
        if (!state.isStreaming) budgetMode = graph.api.getBudgetMode(config)
    }

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
    // The Skyfall countdown, from EITHER route: Speda arming it over chat lands
    // on the view model's channel below, and the Protocols pane hands one up
    // through onArmSkyfall. One piece of state, one screen — so neither route
    // can reach a launch without the clock and its abort.
    var skyfallArm by remember { mutableStateOf<SkyfallArm?>(null) }
    val armedBySpeda by vm.skyfallArm.collectAsStateWithLifecycle()
    LaunchedEffect(armedBySpeda) {
        armedBySpeda?.let { skyfallArm = it; vm.clearSkyfall() }
    }
    var commsOpen by remember { mutableStateOf(false) }
    var boardOpen by remember { mutableStateOf(false) }
    var switcherOpen by remember { mutableStateOf(false) }
    val brand = Brands.BRANDS[agentId] ?: Brands.BRANDS.getValue(Brands.DEFAULT_AGENT)
    val activeTitle = state.sessions.firstOrNull { it.id == state.activeSessionId }?.title

    val listState = rememberLazyListState()

    // Is the transcript parked at the bottom? Anything else means the owner
    // scrolled up to read, and the stream must leave the viewport alone.
    val atBottom by remember {
        derivedStateOf {
            val info = listState.layoutInfo
            val last = info.visibleItemsInfo.lastOrNull() ?: return@derivedStateOf true
            last.index >= info.totalItemsCount - 1 &&
                last.offset + last.size <= info.viewportEndOffset + STICK_SLACK_PX
        }
    }

    // A NEW message (the owner's send, or the assistant's bubble appearing) is a
    // deliberate act — always bring the view down to it.
    LaunchedEffect(state.messages.size) {
        if (state.messages.isNotEmpty()) {
            listState.scrollToItem(state.messages.lastIndex, SCROLL_TO_END)
        }
    }

    // While text streams in, follow the TAIL — and only while the owner is
    // already at the bottom. Scrolling to the item index alone parks the
    // viewport on the message's FIRST line, so every chunk threw the reader back
    // to the top of the answer while the new text grew off-screen below.
    LaunchedEffect(state.messages.lastOrNull()?.content?.length) {
        if (atBottom && state.messages.isNotEmpty()) {
            listState.scrollToItem(state.messages.lastIndex, SCROLL_TO_END)
        }
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
                // Glass INSIDE this backdrop must not blur IT — it would sample
                // the source it lives in, itself included. It refracts the
                // ambient instead: strictly behind, never a feedback loop, and
                // full of moving accent light, so bubbles are real frosted glass
                // rather than the flat occluding-fill fallback.
                CompositionLocalProvider(LocalHazeState provides LocalAmbientHazeState.current) {
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
                budgetMode = budgetMode,
                onBudgetToggle = {
                    val next = !(budgetMode ?: false)
                    budgetMode = next                       // optimistic…
                    scope.launch { budgetMode = graph.api.setBudgetMode(config, next) }  // …then the truth
                },
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
                onArmSkyfall = { armed ->
                    // Close settings first: the countdown is the only thing that
                    // should be on screen while it runs.
                    settingsOpen = false
                    skyfallArm = armed
                },
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

        // SKYFALL — the launch countdown. Last in the stack so it covers every
        // other overlay: while a clock is running, nothing else is the thing on
        // screen. Back is not wired to it; the two outcomes are named on two
        // buttons, and a launch screen you can leave by accident lies about what
        // leaving did.
        skyfallArm?.let { armed ->
            SkyfallCountdown(
                config = config,
                api = graph.api,
                arm = armed,
                onClose = { skyfallArm = null },
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
