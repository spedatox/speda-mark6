// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.MessageCache
import com.speda.heartbreaker.data.MessageJson
import com.speda.heartbreaker.data.SseEvent
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.ChatAction
import com.speda.heartbreaker.domain.ChatMessage
import com.speda.heartbreaker.domain.ChatState
import com.speda.heartbreaker.domain.Role
import com.speda.heartbreaker.domain.UploadedFile
import com.speda.heartbreaker.domain.Watchdog
import com.speda.heartbreaker.domain.reduce
import com.speda.heartbreaker.i18n.AppStrings
import com.speda.heartbreaker.i18n.Tr
import kotlinx.collections.immutable.toPersistentList
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import com.speda.heartbreaker.data.PendingAsk
import com.speda.heartbreaker.data.SkyfallArm
import com.speda.heartbreaker.data.VoiceLevels
import com.speda.heartbreaker.data.VoiceSpeaker
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.util.UUID

/**
 * The chat engine — a Kotlin port of ChatMain.tsx's send/stop/reattach pipeline
 * over the [reduce] store. Holds one [ChatState]; the UI observes [state] and
 * calls the intent methods.
 *
 * Faithful to the web mechanism-for-mechanism: chunk coalescing (one flush per
 * ~frame), the phase-specific watchdog, abort-on-switch, reattach to detached
 * runs, title polling, and the offline transcript mirror.
 */
class ChatViewModel(
    private val api: IgorApi,
    private val cache: MessageCache,
    /** The spoken-output meter. Owned by the graph because it needs a Context
     *  and a session id that outlives any one turn; this only drives it. */
    private val levels: VoiceLevels,
) : ViewModel() {

    private val _state = MutableStateFlow(ChatState())
    val state: StateFlow<ChatState> = _state.asStateFlow()

    /* ── Voice ───────────────────────────────────────────────────────────────
     * Whether replies are SPOKEN. It rides here rather than in [ChatState]
     * because it is not part of the transcript and must not be reduced into one:
     * it is a property of how the owner is using the app right now, and it
     * survives switching session the way the screen brightness does.
     *
     * It changes the turn as well as the playback. A turn sent with this on
     * carries `voice: true` in its client context, which swaps the backend's
     * whole brief: plain spoken prose, and anything that can be SHOWN staged as
     * a window instead of said (igor core/surface.py). */
    private val _voiceOn = MutableStateFlow(false)
    val voiceOn: StateFlow<Boolean> = _voiceOn.asStateFlow()

    /** What the voice is doing — for whatever is drawing the orb. */
    private val _voiceState = MutableStateFlow(VoiceSpeaker.State.IDLE)
    val voiceState: StateFlow<VoiceSpeaker.State> = _voiceState.asStateFlow()

    /** How loud the agent is right now, 0..1 — what the orb reacts to. Zero
     *  throughout when the microphone permission was never granted, which the
     *  orb treats as "animate yourself" rather than as an error. */
    val voiceLevel: StateFlow<Float> get() = levels.level

    /** The speaker for the turn in flight. One per turn: it holds the fence
     *  state of the reply it is filtering, so it can never be reused. */
    private var speaker: VoiceSpeaker? = null

    fun setVoice(on: Boolean) {
        if (_voiceOn.value == on) return
        _voiceOn.value = on
        if (!on) stopSpeaking()
    }

    /** Cut playback without leaving voice mode — the owner has heard enough. */
    fun stopSpeaking() {
        speaker?.stop()
        speaker = null
        levels.stop()
        _voiceState.value = VoiceSpeaker.State.IDLE
    }

    /**
     * A Skyfall countdown Speda has armed, waiting for the shell to draw it.
     *
     * It sits beside [state] rather than inside it because it is not part of the
     * transcript and must never be reduced into one: the countdown is a screen
     * the owner acts on, and a launch you can only stop by scrolling to the
     * right message is not a launch you can stop. The shell collects this, shows
     * the full-screen clock, and calls [clearSkyfall] when it closes.
     *
     * Arming carries no decision. Nothing has been sent when this becomes
     * non-null, and nothing will be unless the clock runs out on screen.
     */
    private val _skyfallArm = MutableStateFlow<SkyfallArm?>(null)
    val skyfallArm: StateFlow<SkyfallArm?> = _skyfallArm.asStateFlow()

    fun clearSkyfall() { _skyfallArm.value = null }

    /**
     * A peer's safety gate stopped an irreversible operation and is waiting on
     * the owner — the SSE-fast path. It sits beside [state] for the same reason
     * [skyfallArm] does: the card is answered by a button, not by scrolling to a
     * message, and it must survive the reduce cycle rather than live inside it.
     *
     * This is the FAST path only. The guaranteed path is
     * [IgorApi.fetchPendingAsks], polled by the global tray mounted at the shell
     * root — a peer raises this event only when the ask carries a chat_id, and a
     * dispatched or background job's ask never carries one.
     */
    private val _pendingAsk = MutableStateFlow<PendingAsk?>(null)
    val pendingAsk: StateFlow<PendingAsk?> = _pendingAsk.asStateFlow()

    fun clearPendingAsk() { _pendingAsk.value = null }

    /** Send the owner's decision down to the peer, then drop the card either way
     *  — a failed answer means the ask is already gone (see [IgorApi.answerAsk]),
     *  not a state worth holding onto and retrying. */
    fun resolveAsk(config: AppConfig, askId: String, approved: Boolean, remember: Boolean) {
        viewModelScope.launch {
            api.answerAsk(config, askId, approved, remember)
            if (_pendingAsk.value?.askId == askId) _pendingAsk.value = null
        }
    }

    /**
     * Supplies the ambient client/platform/location context for a turn, resolved
     * fresh at send time (so location is current and the toggle is honoured). Set
     * by the shell; applied to every send, including regenerate/edit-and-resend.
     */
    var clientContextProvider: (suspend () -> com.speda.heartbreaker.data.ClientContext?)? = null

    /** The owner's chosen interface language — Turkish by default, kept in sync
     *  with the shell's `LocalStrings.current` on every recomposition. Plain
     *  `var`, not a CompositionLocal read: this class isn't @Composable. */
    var strings: AppStrings = Tr

    fun dispatch(action: ChatAction) = _state.update { reduce(it, action) }

    // Singleton handles mirroring the ChatMain refs.
    private var sendJob: Job? = null
    private var reattachJob: Job? = null
    private var runId: String? = null          // request_id of the visible streaming turn (Stop cancels this)
    private var turnSessionId: Int? = null      // which session the in-flight local send belongs to
    private val attached = mutableSetOf<String>() // request_ids already attached to

    // ── Config / sessions ─────────────────────────────────────────────────────

    /** Point the engine at an agent. Cancels any in-flight turn and reloads. */
    fun onConfig(config: AppConfig) {
        if (state.value.config == config) return
        sendJob?.cancel(); reattachJob?.cancel()
        runId = null; turnSessionId = null; attached.clear()
        dispatch(ChatAction.SetConfig(config))
        dispatch(ChatAction.NewChat)
        refreshSessions()
    }

    fun newChat() {
        reattachJob?.cancel()
        if (sendJob?.isActive == true && turnSessionId != null) {
            sendJob?.cancel(); runId = null; turnSessionId = null
        }
        dispatch(ChatAction.NewChat)
    }

    /**
     * Server wins; the cache covers an offline launch. Without this the drawer
     * reads "// NO SESSIONS" with no network and the transcripts already on disk
     * become unreachable.
     */
    private fun refreshSessions() {
        val cfg = state.value.config ?: return
        viewModelScope.launch {
            val cached = cache.loadSessions(cfg.agentId)
            if (cached.isNotEmpty() && state.value.sessions.isEmpty()) {
                dispatch(ChatAction.SetSessions(cached))
            }
            val server = api.fetchSessions(cfg)
            if (server.isNotEmpty()) {
                dispatch(ChatAction.SetSessions(server))
                cache.saveSessions(cfg.agentId, server)
            }
        }
    }

    fun selectSession(sessionId: Int) {
        val cfg = state.value.config ?: return
        reattachJob?.cancel()
        // Abort-on-switch: if a local send is streaming for ANOTHER session, drop
        // the local fetch (the run keeps going server-side; reattach owns it).
        if (sendJob?.isActive == true && turnSessionId != null && turnSessionId != sessionId) {
            sendJob?.cancel(); runId = null; turnSessionId = null
        }
        viewModelScope.launch {
            val server = api.fetchMessages(cfg, sessionId)
            val messages = if (server.isNotEmpty()) {
                cache.save(cfg.agentId, sessionId, server)
                server
            } else {
                cache.load(cfg.agentId, sessionId) ?: emptyList()
            }
            dispatch(ChatAction.SelectSession(sessionId, messages))
            maybeReattach(sessionId, cfg)
        }
    }

    /** Rename a session — optimistic: the title updates locally, then persists.
     * Blank titles are ignored. On failure the next refreshSessions reconciles. */
    fun renameSession(sessionId: Int, title: String) {
        val cfg = state.value.config ?: return
        val trimmed = title.trim()
        if (trimmed.isBlank()) return
        dispatch(ChatAction.UpdateSessionTitle(sessionId, trimmed))
        viewModelScope.launch {
            api.renameSession(cfg, sessionId, trimmed)
            cache.saveSessions(cfg.agentId, state.value.sessions)
        }
    }

    /** Delete a session — optimistic: removed locally (and from the active view if
     * it was open), then deleted server-side and evicted from the cache. */
    fun deleteSession(sessionId: Int) {
        val cfg = state.value.config ?: return
        dispatch(ChatAction.DeleteSession(sessionId))
        viewModelScope.launch {
            api.deleteSession(cfg, sessionId)
            cache.saveSessions(cfg.agentId, state.value.sessions)
        }
    }

    // ── Send / stop ────────────────────────────────────────────────────────────

    fun send(text: String, opts: IgorApi.StreamOpts = IgorApi.StreamOpts()) {
        val cfg = state.value.config ?: return
        if (state.value.isStreaming) return

        if (!opts.regenerate) {
            // The bubble carries what the owner attached: images as data: URLs for
            // display, non-images as chips (the web does exactly this).
            val displayImages = opts.images.map { it.asDataUrl() }.toPersistentList()
            val uploads = opts.documents.map { UploadedFile(it.name, it.size) }.toPersistentList()
            dispatch(
                ChatAction.AddUserMessage(
                    ChatMessage(
                        id = makeId(),
                        role = Role.User,
                        content = text,
                        images = displayImages.takeIf { it.isNotEmpty() },
                        uploads = uploads.takeIf { it.isNotEmpty() },
                    ),
                ),
            )
        }
        val assistantId = makeId()
        dispatch(
            ChatAction.AddAssistantMessage(
                ChatMessage(id = assistantId, role = Role.Assistant, content = "", isStreaming = true, status = strings.chatMain.statusConnecting),
            ),
        )
        val sessionAtSend = state.value.activeSessionId

        var myJob: Job? = null
        myJob = viewModelScope.launch {
            try {
                // Resolve ambient context (platform + opt-in location) for THIS turn.
                val cc = runCatching { clientContextProvider?.invoke() }.getOrNull()
                val sendOpts = if (cc != null) opts.copy(clientContext = cc) else opts
                collectStream(
                    flow = api.streamChat(if (opts.regenerate) "" else text, sessionAtSend, cfg, sendOpts),
                    assistantId = assistantId,
                    fallbackSessionId = sessionAtSend ?: 0,
                    watchdogModel = opts.model ?: "",
                    reattach = {
                        val rid = runId
                        if (rid != null) api.attachStream(cfg, rid) else null
                    },
                ) { doneSessionId ->
                    refreshSessions()
                    pollTitle(doneSessionId, cfg)
                    if (state.value.activeSessionId == doneSessionId) {
                        cache.save(cfg.agentId, doneSessionId, state.value.messages)
                    }
                }
            } finally {
                // Only clear the refs if this turn still owns them (a newer send or
                // the switch-abort may have taken over — never null a live turn).
                if (sendJob === myJob) { runId = null; turnSessionId = null }
            }
        }
        sendJob = myJob
    }

    // ── Message actions (delete / regenerate / edit-and-resend) ─────────────────

    /** Drop a single message from the transcript (DELETE_MESSAGE). */
    fun deleteMessage(id: String) = dispatch(ChatAction.DeleteMessage(id))

    /**
     * Regenerate: keep everything up to and including the user turn, drop the old
     * answer, and re-run on that clean history (keepMessages = the answer's index).
     * The backend truncates its DB rows to match so the model sees the prompt
     * fresh. (ChatMain.handleRegenerate.)
     */
    fun regenerate(assistantId: String, defaultOpts: IgorApi.StreamOpts) {
        val st = state.value
        if (st.isStreaming) return
        val idx = st.messages.indexOfFirst { it.id == assistantId }
        if (idx <= 0) return
        val userMsg = st.messages.getOrNull(idx - 1) ?: return
        if (userMsg.role != Role.User) return
        dispatch(ChatAction.TruncateFrom(assistantId))
        send("", defaultOpts.copy(keepMessages = idx, regenerate = true))
    }

    /**
     * Edit & resend: drop the old user turn + its answer (keepMessages = the user
     * turn's index), then send the edited prompt as a brand-new turn.
     * (ChatMain.handleEditAndResend.)
     */
    fun editAndResend(userId: String, newContent: String, defaultOpts: IgorApi.StreamOpts) {
        val st = state.value
        if (st.isStreaming) return
        val idx = st.messages.indexOfFirst { it.id == userId }
        if (idx < 0) return
        dispatch(ChatAction.TruncateFrom(userId))
        send(newContent, defaultOpts.copy(keepMessages = idx))
    }

    /** Cancel the detached run on the backend, then abort the local fetch. */
    fun stop() {
        val rid = runId
        val cfg = state.value.config
        if (rid != null && cfg != null) viewModelScope.launch { api.cancelRun(cfg, rid) }
        sendJob?.cancel()
        // Stop means stop. The speaker is on viewModelScope precisely so it
        // survives the stream ending, which also means cancelling the stream
        // does not silence it — this has to.
        stopSpeaking()
    }

    override fun onCleared() {
        // Nothing keeps talking into a torn-down screen.
        stopSpeaking()
        super.onCleared()
    }

    // ── Reattach ────────────────────────────────────────────────────────────────

    private fun maybeReattach(sessionId: Int, cfg: AppConfig) {
        // Skip only when the local send streaming right now IS this session's turn.
        if (sendJob?.isActive == true && turnSessionId == sessionId) return
        reattachJob?.cancel()
        reattachJob = viewModelScope.launch {
            val run = api.fetchActiveRuns(cfg, sessionId).firstOrNull() ?: return@launch
            if (!attached.add(run.requestId)) return@launch
            val assistantId = makeId()
            dispatch(
                ChatAction.AddAssistantMessage(
                    ChatMessage(id = assistantId, role = Role.Assistant, content = "", isStreaming = true, status = strings.chatMain.statusReconnecting, sessionId = sessionId),
                ),
            )
            runId = run.requestId
            var settled = false
            try {
                collectStream(
                    flow = api.attachStream(cfg, run.requestId),
                    assistantId = assistantId,
                    fallbackSessionId = sessionId,
                    watchdogModel = null, // attach has no watchdog
                    reattach = {
                        val rid = runId
                        if (rid != null) api.attachStream(cfg, rid) else null
                    },
                    softLanding = true,   // a dropped reattach leaves partial text, no error
                ) { }
                settled = true
            } catch (e: CancellationException) {
                throw e
            } finally {
                if (runId == run.requestId) runId = null
                // No terminal seen → we left mid-run; forget the id so returning re-attaches.
                if (!settled) attached.remove(run.requestId)
            }
        }
    }

    private fun pollTitle(sessionId: Int, cfg: AppConfig) {
        viewModelScope.launch {
            repeat(12) {
                delay(1_500)
                val found = api.fetchSessions(cfg).firstOrNull { it.id == sessionId }
                if (found?.title != null) {
                    dispatch(ChatAction.UpdateSessionTitle(sessionId, found.title))
                    return@launch
                }
            }
        }
    }

    // ── Shared stream consumer (send + attach) ───────────────────────────────────

    /**
     * Consumes an SSE flow into the store with chunk coalescing (~one flush per
     * frame) and, when [watchdogModel] is non-null, the phase-specific watchdog.
     * Dispatches the terminal (done/error) and finalizes a stream that closes
     * without one. Rethrows cancellation after finalizing (keeps streamed text).
     *
     * When [reattach] is non-null and a transient network error drops the stream,
     * the consumer automatically reconnects via the factory (up to
     * [MAX_REATTACH] times) instead of surfacing an error — the turn keeps
     * running server-side and [attachStream] picks up where it left off.
     *
     * [softLanding] suppresses the error dispatch when all reattach attempts are
     * exhausted (the attach itself uses this: a dropped reattach just leaves the
     * partial text rather than showing an error).
     */
    private suspend fun collectStream(
        flow: Flow<SseEvent>,
        assistantId: String,
        fallbackSessionId: Int,
        watchdogModel: String?,
        reattach: (suspend () -> Flow<SseEvent>?)? = null,
        softLanding: Boolean = false,
        onDone: suspend (Int) -> Unit,
    ) = coroutineScope {
        val scope = this
        val pending = StringBuilder()
        var charsSoFar = 0
        var settled = false
        var gotStart = false
        var gotContent = false
        var gotTool = false
        var timedOut = false
        var timeoutReason = ""
        val startedAt = System.currentTimeMillis()
        var lastActivity = startedAt // all touched on the collector thread (Main)

        fun flush() {
            if (pending.isEmpty()) return
            val c = pending.toString()
            pending.setLength(0)
            charsSoFar += c.length
            dispatch(ChatAction.AppendChunk(assistantId, c))
        }

        val flusher = launch { while (isActive) { delay(16); flush() } }

        val watchdog = if (watchdogModel != null) launch {
            val model = Watchdog.modelLabel(watchdogModel, strings)
            while (isActive) {
                delay(Watchdog.TICK_MS)
                if (gotContent) continue // tokens flowing — the cursor is the status now
                val idle = System.currentTimeMillis() - lastActivity
                if (idle >= Watchdog.DEAD_MS) {
                    timedOut = true
                    timeoutReason = Watchdog.timeoutReason(gotStart, gotTool, model, Watchdog.elapsedSeconds(startedAt, System.currentTimeMillis()), strings)
                    scope.cancel()
                } else if (idle >= Watchdog.STALL_MS && !gotTool) {
                    dispatch(ChatAction.SetStatus(assistantId, Watchdog.stallStatus(model, Watchdog.elapsedSeconds(startedAt, System.currentTimeMillis()), strings)))
                }
            }
        } else {
            null
        }

        var currentFlow = flow
        var reattachAttempts = 0

        while (true) {
            try {
                currentFlow.collect { event ->
                    lastActivity = System.currentTimeMillis()
                    when (event.type) {
                        "start" -> {
                            gotStart = true
                            // A fresh speaker per turn: it carries the fence
                            // state of the reply it filters, so reusing one
                            // would have it judging turn N+1 against whether
                            // turn N ended inside a code block.
                            if (_voiceOn.value) {
                                val cfg = state.value.config
                                if (cfg != null) {
                                    // viewModelScope, NOT this turn's stream
                                    // scope: speech outlives the stream by
                                    // design — the last sentence is still
                                    // playing when the reply finishes arriving.
                                    // Parented to the stream, `coroutineScope`
                                    // would wait for playback before the turn
                                    // could return.
                                    speaker = VoiceSpeaker(
                                        api, cfg, cfg.agentId, viewModelScope,
                                        levels.sessionId,
                                    ) { st ->
                                        _voiceState.value = st
                                        // Speech ending on its own releases the
                                        // meter too — stopSpeaking is not the
                                        // only way a turn goes quiet.
                                        if (st == VoiceSpeaker.State.IDLE) levels.stop()
                                    }
                                    // Metering runs only while there is
                                    // something to meter — a Visualizer left
                                    // enabled is a hardware effect held open
                                    // across an idle conversation.
                                    levels.start()
                                }
                            }
                            runId = event.requestId
                            if (event.sessionId != 0) {
                                turnSessionId = event.sessionId
                                dispatch(ChatAction.TagMessageSession(assistantId, event.sessionId))
                            }
                            dispatch(ChatAction.SetStatus(assistantId, strings.chatMain.statusThinking))
                        }
                        "chunk" -> {
                            gotContent = true
                            strOf(event.data)?.let {
                                pending.append(it)
                                // Fed raw. The speaker does its own line-holding
                                // and fence tracking — it has to, because a
                                // chunk boundary lands mid-word far more often
                                // than it lands on a sentence.
                                speaker?.feed(it)
                            }
                        }
                        "tool" -> {
                            gotTool = true
                            flush() // charsSoFar must reflect everything seen before this tool
                            MessageJson.toolFrom(event.data)?.let {
                                dispatch(ChatAction.AddTool(assistantId, it.copy(afterChars = charsSoFar)))
                            }
                        }
                        "tool_result" -> {
                            (event.data as? JsonObject)?.let { o ->
                                val id = strOf(o["id"])
                                if (id != null) dispatch(ChatAction.SetToolResult(assistantId, id, strOf(o["result"]).orEmpty()))
                            }
                        }
                        "file" -> MessageJson.fileFrom(event.data)?.let { dispatch(ChatAction.AddFile(assistantId, it)) }
                        // A coding peer (Optimus, Centurion) delegated part of
                        // this turn. Goes to its own panel, never into `content`
                        // — see ChatAction.Subagent's doc for why.
                        "subagent" ->
                            (event.data as? JsonObject)?.let { dispatch(ChatAction.Subagent(assistantId, it)) }
                        // Speda armed a launch project. Unlike `house_party_auth`
                        // below, this one IS handled here: the phone draws the
                        // countdown, so the backend permits arming from it
                        // (app/core/surface.py). The event carries no decision —
                        // nothing has been sent, and the clock on screen is what
                        // decides whether anything is.
                        "skyfall_arm" ->
                            MessageJson.skyfallArmFrom(event.data)?.let { _skyfallArm.value = it }
                        // A peer's gate stopped an irreversible operation and is
                        // waiting. This is the fast path only — see [pendingAsk]'s
                        // doc for why the global tray's poll is the guaranteed one.
                        "permission_request" ->
                            MessageJson.permissionAskFrom(event.data)?.let { _pendingAsk.value = it }
                        // `house_party_auth` is deliberately unhandled: the
                        // protocol is a desktop surface, and the backend refuses
                        // to engage it from here before any authorization window
                        // would ever be raised. Falling through to the else is
                        // the correct behaviour, not an omission.
                        "done" -> {
                            flush(); settled = true
                            // Flush the last part-sentence, then let playback
                            // drain on its own: the turn is over, the SPEECH is
                            // not, and cutting it here would clip the last
                            // sentence of every reply.
                            speaker?.finish()
                            speaker = null
                            dispatch(ChatAction.FinishMessage(assistantId, event.sessionId))
                            onDone(event.sessionId)
                        }
                        "error" -> {
                            flush(); settled = true
                            // Nothing half-spoken survives a failed turn.
                            stopSpeaking()
                            dispatch(ChatAction.ErrorMessage(assistantId, strOf(event.data) ?: strings.chatMain.turnFailed))
                        }
                    }
                }
                // Stream closed without a terminal — finalize so the bubble never sticks.
                if (!settled) { flush(); settled = true; dispatch(ChatAction.FinishMessage(assistantId, fallbackSessionId)) }
                break
            } catch (e: CancellationException) {
                flush()
                if (timedOut) {
                    dispatch(ChatAction.ErrorMessage(assistantId, timeoutReason.ifEmpty { strings.chatMain.timedOutFallback }))
                } else if (!settled) {
                    // User-initiated stop or a view switch — keep whatever streamed.
                    dispatch(ChatAction.FinishMessage(assistantId, fallbackSessionId))
                }
                throw e
            } catch (e: java.net.SocketException) {
                if (reattach != null && reattachAttempts < MAX_REATTACH) {
                    reattachAttempts++
                    gotContent = false; gotTool = false
                    lastActivity = System.currentTimeMillis()
                    dispatch(ChatAction.SetStatus(assistantId, "${strings.chatMain.statusReconnecting}…"))
                    val newFlow = reattach()
                    if (newFlow != null) { currentFlow = newFlow; continue }
                }
                flush(); settled = true
                if (softLanding) {
                    dispatch(ChatAction.FinishMessage(assistantId, fallbackSessionId))
                } else {
                    dispatch(ChatAction.ErrorMessage(assistantId, strings.chatMain.networkError))
                }
                break
            } catch (e: Exception) {
                val msg = e.message.orEmpty()
                val net = NET_ERROR.containsMatchIn(msg)
                if (net && reattach != null && reattachAttempts < MAX_REATTACH) {
                    reattachAttempts++
                    gotContent = false; gotTool = false
                    lastActivity = System.currentTimeMillis()
                    dispatch(ChatAction.SetStatus(assistantId, "${strings.chatMain.statusReconnecting}…"))
                    val newFlow = reattach()
                    if (newFlow != null) { currentFlow = newFlow; continue }
                }
                flush(); settled = true
                if (net && softLanding) {
                    dispatch(ChatAction.FinishMessage(assistantId, fallbackSessionId))
                } else {
                    dispatch(ChatAction.ErrorMessage(assistantId,
                        if (net) strings.chatMain.networkError
                        else msg.ifEmpty { strings.chatMain.requestFailed }))
                }
                break
            }
        }
        // The finally block below runs once, after the loop exits or the scope is cancelled.
        try {
            // no-op — the block below is the actual finally
        } finally {
            watchdog?.cancel(); flusher.cancel(); flush()
        }
    }

    private fun strOf(e: JsonElement?): String? {
        val p = e as? JsonPrimitive ?: return null
        if (p is JsonNull) return null
        return p.content // chunk/error/tool_result fields are strings
    }

    private fun makeId(): String = UUID.randomUUID().toString().replace("-", "").take(8)

    private companion object {
        /** Max automatic reattach attempts when the SSE stream drops on a
         * transient network error (mobile network handoff, brief disconnection).
         * The turn keeps running server-side; each attempt calls [IgorApi.attachStream]
         * to pick up where it left off. */
        const val MAX_REATTACH = 3

        val NET_ERROR = Regex(
            "failed to fetch|networkerror|load failed|err_connection|unable to resolve|connection refused|timeout|timed out|connection abort|software caused|socketexception|econnreset|broken pipe",
            RegexOption.IGNORE_CASE,
        )
    }
}
