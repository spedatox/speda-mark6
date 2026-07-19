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
import kotlinx.collections.immutable.toPersistentList
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
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
) : ViewModel() {

    private val _state = MutableStateFlow(ChatState())
    val state: StateFlow<ChatState> = _state.asStateFlow()

    /**
     * Supplies the ambient client/platform/location context for a turn, resolved
     * fresh at send time (so location is current and the toggle is honoured). Set
     * by the shell; applied to every send, including regenerate/edit-and-resend.
     */
    var clientContextProvider: (suspend () -> com.speda.heartbreaker.data.ClientContext?)? = null

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
                ChatMessage(id = assistantId, role = Role.Assistant, content = "", isStreaming = true, status = "Connecting"),
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
                    ChatMessage(id = assistantId, role = Role.Assistant, content = "", isStreaming = true, status = "Reconnecting", sessionId = sessionId),
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
                ) { }
                settled = true
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                // network drop on the attach — leave the streamed text; run lives on
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
     */
    private suspend fun collectStream(
        flow: Flow<SseEvent>,
        assistantId: String,
        fallbackSessionId: Int,
        watchdogModel: String?,
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
            val model = Watchdog.modelLabel(watchdogModel)
            while (isActive) {
                delay(Watchdog.TICK_MS)
                if (gotContent) continue // tokens flowing — the cursor is the status now
                val idle = System.currentTimeMillis() - lastActivity
                if (idle >= Watchdog.DEAD_MS) {
                    timedOut = true
                    timeoutReason = Watchdog.timeoutReason(gotStart, gotTool, model, Watchdog.elapsedSeconds(startedAt, System.currentTimeMillis()))
                    scope.cancel()
                } else if (idle >= Watchdog.STALL_MS && !gotTool) {
                    dispatch(ChatAction.SetStatus(assistantId, Watchdog.stallStatus(model, Watchdog.elapsedSeconds(startedAt, System.currentTimeMillis()))))
                }
            }
        } else {
            null
        }

        try {
            flow.collect { event ->
                lastActivity = System.currentTimeMillis()
                when (event.type) {
                    "start" -> {
                        gotStart = true
                        runId = event.requestId
                        if (event.sessionId != 0) {
                            turnSessionId = event.sessionId
                            dispatch(ChatAction.TagMessageSession(assistantId, event.sessionId))
                        }
                        dispatch(ChatAction.SetStatus(assistantId, "Thinking"))
                    }
                    "chunk" -> {
                        gotContent = true
                        strOf(event.data)?.let { pending.append(it) }
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
                    "done" -> {
                        flush(); settled = true
                        dispatch(ChatAction.FinishMessage(assistantId, event.sessionId))
                        onDone(event.sessionId)
                    }
                    "error" -> {
                        flush(); settled = true
                        dispatch(ChatAction.ErrorMessage(assistantId, strOf(event.data) ?: "The turn failed."))
                    }
                }
            }
            // Stream closed without a terminal — finalize so the bubble never sticks.
            if (!settled) { flush(); settled = true; dispatch(ChatAction.FinishMessage(assistantId, fallbackSessionId)) }
        } catch (e: CancellationException) {
            flush()
            if (timedOut) {
                dispatch(ChatAction.ErrorMessage(assistantId, timeoutReason.ifEmpty { "The backend went silent and the request timed out." }))
            } else if (!settled) {
                // User-initiated stop or a view switch — keep whatever streamed.
                dispatch(ChatAction.FinishMessage(assistantId, fallbackSessionId))
            }
            throw e
        } catch (e: Exception) {
            flush(); settled = true
            val msg = e.message.orEmpty()
            val net = NET_ERROR.containsMatchIn(msg)
            dispatch(
                ChatAction.ErrorMessage(
                    assistantId,
                    if (net) "Couldn't reach the backend — network error. Is the API server running and reachable from this host?"
                    else msg.ifEmpty { "The request failed." },
                ),
            )
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
        val NET_ERROR = Regex(
            "failed to fetch|networkerror|load failed|err_connection|unable to resolve|connection refused|timeout|timed out",
            RegexOption.IGNORE_CASE,
        )
    }
}
