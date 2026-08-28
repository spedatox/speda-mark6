package com.speda.heartbreaker.i18n

import androidx.compose.runtime.staticCompositionLocalOf

/**
 * The mobile client's interface-language dictionary — the Kotlin counterpart of
 * heartbreaker's `lib/i18n/{en,tr}.ts`. One shape, two instances (`En`/`Tr`):
 * every constructor parameter here is required, so a translation missing from
 * either object is a compile error, never a silent fallback to English at
 * runtime. Namespaced by screen/file, same as the desktop dictionary, so a
 * string's origin is obvious from its path (`sidebar.newConversation`,
 * `settingsData.importTitle`, …).
 *
 * Deliberately narrower than the desktop dict: the "Stark HUD" readouts (DIAG,
 * MATERIALIZING, EMERGENCY, the map/aircraft/chart cards, the Systems Board) stay
 * English on both platforms by design — see HudStrip.kt and the systems/ package
 * — so those screens carry no entries here at all.
 */
data class AppStrings(
    val common: Common,
    val uplink: Uplink,
    val composer: Composer,
    val fileCard: FileCard,
    val message: Message,
    val commsTray: CommsTray,
    val sidebar: Sidebar,
    val welcome: Welcome,
    val agentSwitcher: AgentSwitcher,
    val settingsTabs: SettingsTabs,
    val settingsGeneral: SettingsGeneral,
    val settingsAccount: SettingsAccount,
    val settingsData: SettingsData,
    val settingsConnections: SettingsConnections,
    val settingsAutomations: SettingsAutomations,
    val settingsVoices: SettingsVoices,
    val settingsReminders: SettingsReminders,
    val settingsHealth: SettingsHealth,
    val settingsConfig: SettingsConfig,
    val settingsInterface: SettingsInterface,
    val protocols: Protocols,
    val skyfall: Skyfall,
    val chatMain: ChatMain,
    val toolStatus: ToolStatus,
    val hud: Hud,
    val toolFeed: ToolFeed,
    val proseKind: ProseKind,
    val aircraft: Aircraft,
    val bus: Bus,
    val calendar: Calendar,
    val codeBlock: CodeBlock,
    val mapCard: MapCard,
    val systemsBoard: SystemsBoard,
    val knowledgeBank: KnowledgeBank,
    val routingMatrix: RoutingMatrix,
    val gallery: Gallery,
) {
    data class Common(
        val cancel: String,
        val save: String,
        val edit: String,
        val delete: String,
        val remove: String,
        val close: String,
        val connect: String,
        val disconnect: String,
        val connected: String,
        val show: String,
        val hide: String,
    )

    data class Uplink(
        val establishUplink: String,
        val apiBaseLabel: String,
        val apiKeyLabel: String,
        val connect: String,
    )

    data class Composer(
        val placeholder: String,
        val canMakeMistakes: (String) -> String,
        val photos: String,
        val files: String,
        val voiceInput: String,
        val budgetFrugal: String,
        val budgetFull: String,
        val budgetUnknown: String,
        val noModelsReported: String,
    )

    data class FileCard(
        val download: String,
        val saved: String,
    )

    data class Message(
        val saveAndSend: String,
        val somethingWentWrong: String,
        val thinking: String,
    )

    /** [com.speda.heartbreaker.domain.ToolStatus] is plain Kotlin, not
     *  @Composable — MessageItem hands it this map instead of it reading
     *  `LocalStrings` itself. Keyed exactly like the desktop's
     *  `message.toolStatus`. */
    data class ToolStatus(
        val labels: Map<String, String>,
        val usingTool: (String) -> String,
    )

    data class CommsTray(
        val agentTraffic: String,
        val working: (Int) -> String,
        val messages: (Int) -> String,
        val retract: String,
        val expand: String,
        val noTraffic: String,
        val linking: String,
        val workingEllipsis: (String) -> String,
        val less: String,
        val more: String,
    )

    data class Sidebar(
        val newConversation: String,
        val noResults: String,
        val noSessions: String,
        val settings: String,
        val comms: String,
        val systemsBoard: String,
        val resetUplink: String,
        val rename: String,
        val delete: String,
        val confirmDelete: String,
        val searchSessions: String,
        val newLink: String,
        val groupToday: String,
        val groupYesterday: String,
        val groupWeek: String,
        val groupMonth: String,
        val groupOlder: String,
    )

    data class Welcome(
        val goodMorning: String,
        val goodAfternoon: String,
        val goodEvening: String,
        val allHandsOnDeck: (String) -> String,
        val allHandsOnDeckBare: String,
    )

    data class AgentSwitcher(
        val eyebrow: String,
        val title: String,
        val hint: String,
    )

    data class TabInfo(val label: String, val blurb: String)

    data class SettingsTabs(
        val title: String,
        val general: TabInfo,
        val config: TabInfo,
        val connections: TabInfo,
        val automations: TabInfo,
        val voices: TabInfo,
        val reminders: TabInfo,
        val health: TabInfo,
        val interfaceTab: TabInfo,
        val protocols: TabInfo,
        val data: TabInfo,
        val account: TabInfo,
    )

    data class SettingsGeneral(
        val systemPrompt: String,
        val systemPromptHint: String,
        val systemPromptPlaceholder: String,
        val temperature: String,
        val sampling: String,
        val temperatureHint: String,
        val precise: String,
        val creative: String,
        val behaviour: String,
        val budgetMode: String,
        val budgetModeHint: String,
    )

    data class SettingsAccount(
        val yourName: String,
        val yourNameHint: String,
        val namePlaceholder: String,
        val uplink: String,
        val backend: String,
        val resetHint: String,
        val resetUplink: String,
    )

    data class SettingsData(
        val importTitle: String,
        val importDesc: String,
        val importing: String,
        val chooseZip: String,
        val uploadingStarting: String,
        val couldntRead: String,
        val indexTitle: String,
        val indexDesc: String,
        val indexing: String,
        val indexHistory: String,
        val indexingStarted: String,
        val importFailedHttp: (Int) -> String,
        val importStartedBackground: String,
        val importFailedError: (String) -> String,
        val indexingStartedBackground: String,
        val indexFailedError: (String) -> String,
    )

    data class SettingsConnections(
        val managedAccounts: String,
        val googleConnected: String,
        val googleDisconnected: String,
        val notionConnected: String,
        val notionDisconnected: String,
        val openingSignIn: String,
        val couldntStartSignIn: String,
        val finishInBrowser: String,
        val toolBudget: String,
        val activeToolTokens: String,
        val overLimit: String,
        val toolsets: String,
        val noMcpServers: String,
        val offline: String,
        val needs: (String) -> String,
        val toolsAlwaysOn: (Int) -> String,
        val toolsOnDemand: (Int) -> String,
        val microsoftConnected: String,
        val microsoftDisconnected: String,
        /** Header for the owner-registered MCP servers (GET/POST /connections/mcp)
         *  — a DIFFERENT list from [toolsets] above, which is the engine's own
         *  managed toolset toggles (GET /connections). */
        val customServers: String,
        val noCustomServers: String,
        val addServer: String,
        val serverSaveFailed: (String) -> String,
        /** Header for the owner's saved logins (GET/POST /connections/portals). */
        val portalsSection: String,
        val noPortals: String,
        val addPortal: String,
        /** The Playwright container never configured on this deployment
         *  (BROWSER_URL unset) — distinct from `down`, which is configured but
         *  unreachable right now. */
        val browserContainerOff: String,
        val browserContainerDown: (String) -> String,
        val signIn: String,
        val forgetSession: String,
        val hasSession: String,
        val noSession: String,
    )

    data class SettingsAutomations(
        val pipeline: String,
        val n8nEngine: String,
        val checking: String,
        val n8nNeedsKey: String,
        val n8nUnreachable: String,
        val telegramDelivery: String,
        val telegramNeedsToken: String,
        val telegramConnected: String,
        val telegramReady: String,
        val connectTelegram: String,
        val openingTelegram: String,
        val couldntStartConnect: String,
        val tapStart: String,
        val noResponseYet: String,
        val watchers: String,
        val nothingWatched: String,
        val footer: String,
        // ── The builder (mirror of AutomationBuilder.tsx) ──────────────────
        val add: String,
        val edit: String,
        val newTitle: String,
        val editTitle: String,
        val stepType: String,
        val tplBriefing: String,
        val tplBriefingDesc: String,
        val tplOnce: String,
        val tplOnceDesc: String,
        val tplAsk: String,
        val tplAskDesc: String,
        val stepAgent: String,
        val stepAgentHint: String,
        val stepWhen: String,
        val frequency: String,
        val time: String,
        val date: String,
        val dayOfMonth: String,
        val weekdays: String,
        val freqOnce: String,
        val freqDaily: String,
        val freqWeekly: String,
        val freqMonthly: String,
        val dayShort: List<String>,
        val shortMonthWarning: String,
        val stepIntent: String,
        val intentHint: String,
        val intentPlaceholder: String,
        val answerButtons: String,
        val answerButtonsHint: String,
        val addButton: String,
        val repeatEvery: String,
        val maxAsks: String,
        val statusRaw: String,
        val statusFailed: String,
        val yourWords: String,
        val instructionLabel: String,
        val save: String,
        val create: String,
        val cancel: String,
        val saving: String,
        val nameLabel: String,
        val namePlaceholder: String,
        val dayFlags: String,
        val dayFlagsHint: String,
        val flagLabelPlaceholder: String,
        val addFlag: String,
        // ── Hooks — the three event-driven watcher templates ───────────────
        val stepHookType: String,
        val stepHookTypeHint: String,
        val tplHookKeyword: String,
        val tplHookKeywordDesc: String,
        val tplHookAddress: String,
        val tplHookAddressDesc: String,
        val tplHookMail: String,
        val tplHookMailDesc: String,
        val stepHookConfig: String,
        val hookUrl: String,
        val hookKeyword: String,
        val hookKeywordHint: String,
        val hookKeywordPlaceholder: String,
        val hookAddressNote: String,
        val mailDomain: String,
        val mailDomainHint: String,
        val mailDomainPlaceholder: String,
        val checkEvery: String,
        val checkEveryHint: String,
        val everyMinutesShort: (Int) -> String,
        val voiceReply: String,
        val voiceReplyHint: String,
        // ── List-row actions ────────────────────────────────────────────────
        val test: String,
        val testSending: String,
        val testSent: String,
        val testFailed: String,
        val deleteWatcherTitle: String,
        val activeClickPause: String,
        val pausedClickResume: String,
        val lastFired: (String) -> String,
        val saveFailed: (String) -> String,
    )

    data class SettingsVoices(
        val title: String,
        val blurb: String,
        val tuned: String,
        val voicePick: String,
        val voicePickHint: String,
        val useDefault: String,
        val manualVoiceId: String,
        val manualVoiceIdHint: String,
        val manualVoiceIdPlaceholder: String,
        val tuning: String,
        val tuningHint: String,
        val stability: String,
        val variable: String,
        val stable: String,
        val similarity: String,
        val low: String,
        val high: String,
        val style: String,
        val none: String,
        val exaggerated: String,
        val speed: String,
        val slower: String,
        val faster: String,
        val speakerBoost: String,
        val resetAll: String,
    )

    data class SettingsReminders(
        val title: String,
        val intro: String,
        val noneConfigured: String,
        val everyDay: String,
        val dayLabels: List<String>,
        val newReminder: String,
        val newReminderTitle: String,
        val editReminder: (String) -> String,
        val idLabel: String,
        val messageLabel: String,
        val timeLabel: String,
        val reaskLabel: String,
        val giveUpLabel: String,
        val daysLabel: String,
        val askedByLabel: String,
        val answerButtonsLabel: String,
        val addButton: String,
        val idRequired: String,
        val saving: String,
        val save: String,
        val cancel: String,
        val delete: String,
        val saveFailed: String,
        val recent: String,
        val noAnswer: String,
        val defaultDoneLabel: String,
    )

    data class SettingsHealth(
        val title: String,
        val unsupported: String,
        val notInstalled: String,
        val getHealthConnect: String,
        val syncToggleLabel: String,
        val syncToggleHint: String,
        val nothingGranted: String,
        val backfilling: (Int) -> String,
        val syncPaused: String,
        val dataTypes: String,
        val dataTypesHint: String,
        val manageGrants: String,
        val notGranted: String,
        val syncSection: String,
        val lastSync: (String) -> String,
        val neverSynced: String,
        val onServer: (Int) -> String,
        val syncing: String,
        val syncNow: String,
        val disconnectWipe: String,
        val disconnected: String,
        val disconnectFailed: String,
        val syncedRecords: (Int, Int, Int) -> String,
        val upToDate: String,
        val notPermitted: String,
        val healthConnectUnavailable: String,
        val batchRejected: String,
        val typeSteps: String,
        val typeSleep: String,
        val typeHeartRate: String,
        val typeExercise: String,
        val typeWeight: String,
        val typeOxygen: String,
    )

    data class SettingsConfig(
        val intro: String,
        val loading: String,
        val searchPlaceholder: String,
        val groupEdited: (Int) -> String,
        val restart: String,
        val editedDot: String,
        val revert: String,
        val storedTypeReplace: (String) -> String,
        val notSet: String,
        val clearStored: String,
        val unsavedChanges: (Int) -> String,
        val noChanges: String,
        val discard: String,
        val saving: String,
        val saveChanges: String,
        val appliedLive: (Int) -> String,
        val needsRestartN: (Int) -> String,
        val rejected: (Int) -> String,
        val sourceOfTruthTitle: String,
        val sourceOfTruthHint: String,
        val default: (String) -> String,
        val none: String,
        val saveFailedBackend: String,
    )


    /** The Protocols pane — the six standing modes, and why one of them is grey. */
    data class Protocols(
        val loading: String,
        val notEnabled: String,
        val unreachable: String,
        val unreachableHint: String,
        val throughOrion: String,
        val lastAction: String,
        // Lockdown
        val lockdownTitle: String,
        val lockdownDesc: String,
        val lockdownDisabled: String,
        val containmentActive: String,
        val containmentInactive: String,
        val portsSealed: String,
        val acceptingNormally: String,
        val standDown: String,
        val containmentStoodDown: String,
        val firewallRules: String,
        val sealedLabel: String,
        val openLabel: String,
        // Lifeboat
        val lifeboatTitle: String,
        val lifeboatDesc: String,
        val lifeboatDisabled: String,
        val lifeboatLevel: (String) -> String,
        val hostHealthy: String,
        val hostResources: String,
        val underPressure: String,
        val hostUnreadable: String,
        val hostUnreadableHint: String,
        // Octavius
        val octaviusTitle: String,
        val octaviusDesc: String,
        val octaviusDisabled: String,
        val noBackups: String,
        val backupsStale: String,
        val backupsHealthy: String,
        val nothingToRestoreFrom: String,
        val backupLatest: (String, Double, Double?, Int) -> String,
        val backupNow: String,
        val backingUp: String,
        val backupDone: String,
        val backupFailed: String,
        // Doormat
        val doormatTitle: String,
        val doormatDesc: String,
        val doormatDisabled: String,
        val noMoveInProgress: String,
        val movePreparing: String,
        val moveCutOver: String,
        val servingDomain: (String) -> String,
        val consoleChecklist: String,
        val addDoNotReplace: String,
        val restartOutstanding: String,
        val restartOutstandingHint: String,
        // Skyfall
        val skyfallTitle: String,
        val skyfallDesc: String,
        val skyfallEmpty: String,
        val arm: String,
        val armFailed: String,
        val edit: String,
        val delete: String,
        val addProject: String,
        val seconds: (Int) -> String,
        val fieldName: String,
        val namePlaceholder: String,
        val fieldDescription: String,
        val descriptionHint: String,
        val fieldMethod: String,
        val fieldUrl: String,
        val fieldCountdown: String,
        val countdownHint: String,
        val fieldBody: String,
        val bodyHint: String,
        val fieldHeaders: String,
        val headersHint: String,
        val save: String,
        val cancel: String,
        // House Party — shown so it can say it is not available here
        val housePartyTitle: String,
        val housePartyDesc: String,
        val housePartyPhoneHint: String,
        val desktopOnly: String,
        val desktopOnlyTag: String,
    )

    /** The Skyfall countdown itself. */
    data class Skyfall(
        val armed: String,
        val firing: String,
        val aborted: String,
        val stalled: String,
        val complete: String,
        val willFire: String,
        val sending: String,
        val abortedBody: String,
        val stalledBody: String,
        val abort: String,
        val fireNow: String,
        val close: String,
        val notSent: String,
        val delivered: (Int) -> String,
        val rejected: (Int) -> String,
        val failed: String,
        val truncated: String,
    )

    data class SettingsInterface(
        val theme: String,
        val dark: String,
        val lightSoon: String,
        val themeHint: String,
        val locationAwareness: String,
        val shareLocation: String,
        val shareLocationHint: String,
        val locationFooter: String,
        val display: String,
        val displayHint: String,
        val language: String,
        val languageHint: String,
    )

    /** [com.speda.heartbreaker.ui.chat.ChatViewModel] and
     *  [com.speda.heartbreaker.domain.Watchdog] are plain Kotlin, not
     *  @Composable, so they can't read [LocalStrings] — the shell hands them
     *  this dict directly (`ChatViewModel.strings`) instead. */
    data class ChatMain(
        val statusConnecting: String,
        val statusThinking: String,
        val statusReconnecting: String,
        val turnFailed: String,
        val timedOutFallback: String,
        val networkError: String,
        val requestFailed: String,
        val modelFallback: String,
        val waitingOnModel: (String, Int) -> String,
        val timeoutNoAck: (Int) -> String,
        val timeoutToolStuck: (Int) -> String,
        val timeoutNoStream: (String, Int) -> String,
    )

    data class Hud(
        val diag: String,
        val link: String,
        val model: String,
        val host: String,
        val tools: String,
        val rtt: String,
        val sess: String,
        val date: String,
        val time: String,
        val online: String,
        val offline: String,
    )

    data class ToolFeed(
        val steps: (Int) -> String,
        val running: String,
        val failed: (Int) -> String,
    )

    /** Shared by every ```fence card (aircraft/bus/calendar/chart/map/svg/html) for
     *  the "still streaming" and "couldn't parse" placeholders, and each card's own
     *  short kind label used inside them. */
    data class ProseKind(
        val materializing: String,
        val parseError: String,
        val aircraft: String,
        val bus: String,
        val calendar: String,
        val chart: String,
        val map: String,
        val widget: String,
        val diagram: String,
        val svg: String,
        val desktopOnlyNotice: String,
    )

    data class Aircraft(
        val emergency: String,
        val signalLost: String,
        val onGround: String,
        val airborne: String,
        val type: String,
        val alt: String,
        val ground: String,
        val speed: String,
        val heading: String,
        val squawk: String,
        val emergencySquawkPrefix: String,
        val staleWarning: String,
        val live: String,
        val paused: String,
        val resumeHint: String,
        val pauseHint: String,
        val landedPausedWarning: String,
        val signalLostPausedWarning: String,
        val pausedManualWarning: String,
    )

    data class Bus(
        val busStop: (String) -> String,
        val live: (Int) -> String,
        val schedule: String,
    )

    data class Calendar(
        val weekdays: List<String>,
        val months: List<String>,
        val fallbackTitle: String,
    )

    data class CodeBlock(
        val copy: String,
        val copied: String,
        val documentSuffix: String,
    )

    data class MapCard(
        val fallbackTitle: String,
        val km: String,
        val min: String,
        val found: (Int) -> String,
        val fastest: String,
        val route: String,
        val trafficDelay: (Int) -> String,
        val vsFreeFlow: (Int) -> String,
        val shrink: String,
        val expand: String,
        val open: String,
        val closed: String,
        val steps: (Int) -> String,
        val navigate: String,
        val navigateTo: (String) -> String,
        val openInMaps: String,
        val hours: String,
        val website: String,
        val googlePage: String,
        val openingGoogleMapsIn: (Int) -> String,
        val cancel: String,
        val trafficSlow: String,
        val trafficJam: String,
        val trafficClear: String,
    )

    data class SystemsBoard(
        val uplinkStatus: String,
        val link: String,
        val online: String,
        val deny: String,
        val host: String,
        val rtt: String,
        val toolsReg: String,
        val sessions: String,
        val budgetMode: String,
        val engaged: String,
        val off: String,
        val ollamaNode: String,
        val localActive: String,
        val notDetected: String,
        val forgeLink: String,
        val inProcess: String,
        val networkNodes: String,
        val noNodes: String,
        val mediaDisconnected: String,
        val linkedTools: (Int) -> String,
        val standby: String,
        val coreRoutingMatrix: String,
        val tokenBudget: String,
        val prefixSaturation: String,
        val responseTrace: String,
        val awaitingTelemetry: String,
        val rttProbe: String,
        val dataBanks: String,
    )

    data class KnowledgeBank(
        val noRecords: String,
        val filesFolders: (Int, Int) -> String,
        val editable: String,
        val cancel: String,
        val saving: String,
        val commit: String,
        val closeHistory: String,
        val history: String,
        val edit: String,
        val saved: String,
        val changedOnServer: String,
        val saveFailed: String,
        val restored: String,
        val restoreFailed: String,
        val lastWrite: (String) -> String,
        val emptyFile: String,
        val noRevisionsYet: String,
        val restore: String,
        /** GET /admin/memory/status — where the observation record stands. */
        val observations: (Int) -> String,
        val atRiskFacts: (Int) -> String,
        val rebuildingMemory: String,
        val lastRebuildFailed: String,
    )

    data class RoutingMatrix(
        val cores: (Int) -> String,
        val profileDefault: String,
        val notFound: String,
        val contextShards: String,
        val linkedCount: (Int, Int) -> String,
        val noShards: String,
        val agentCores: String,
        val allOnProfile: String,
        val pinned: (Int) -> String,
        val legionCores: String,
        val envPin: (String) -> String,
        val allOnEffortPolicy: String,
        val deploymentPinNotice: (String) -> String,
        val profileLabel: (String) -> String,
        val pinnedTo: (String) -> String,
        val effortLabel: (String, String) -> String,
    )

    data class Gallery(
        val themeEngine: String,
        val standDown: String,
        val housePartyBtn: String,
        val resetUplink: String,
        val palette: (String) -> String,
        val glassMaterial: String,
        val default: String,
        val active: String,
        val amber: String,
        val tint: String,
        val etchedSeams: String,
        val seamDesc: String,
        val typeRamp: String,
        val bodyCopyDemo: String,
    )
}

/** `Locale.TR` / `Locale.EN` — the interface language, Turkish by default. */
enum class AppLocale(val wire: String, val label: String) {
    TR("tr", "Türkçe"),
    EN("en", "English"),
}

fun localeFromWire(wire: String): AppLocale = AppLocale.entries.firstOrNull { it.wire == wire } ?: AppLocale.TR

/** The dictionary for the owner's chosen interface language — Turkish by
 *  default. Read as `LocalStrings.current.sidebar.newConversation` from any
 *  composable, provided once near the app root from the persisted setting. */
val LocalStrings = staticCompositionLocalOf<AppStrings> { Tr }
