package com.speda.heartbreaker.domain

import androidx.compose.runtime.Immutable
import kotlinx.collections.immutable.PersistentList
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toPersistentList
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull

/** The backend connection + which agent this session targets (lib/types AppConfig). */
@Immutable
data class AppConfig(val apiBase: String, val apiKey: String, val agentId: String)

@Immutable
data class ChatState(
    val config: AppConfig? = null,
    val sessions: PersistentList<Session> = persistentListOf(),
    val activeSessionId: Int? = null,
    val messages: PersistentList<ChatMessage> = persistentListOf(),
    val isStreaming: Boolean = false,
)

/**
 * The 19 reducer actions — same names/semantics as store/chat.ts. Sealed so the
 * `when` in [reduce] is exhaustive.
 */
sealed interface ChatAction {
    data class SetConfig(val config: AppConfig) : ChatAction
    data class SetSessions(val sessions: List<Session>) : ChatAction
    data class SelectSession(val sessionId: Int, val messages: List<ChatMessage>) : ChatAction
    data object NewChat : ChatAction
    data class AddUserMessage(val message: ChatMessage) : ChatAction
    data class AddAssistantMessage(val message: ChatMessage) : ChatAction
    data class AppendChunk(val id: String, val chunk: String) : ChatAction
    data class SetStatus(val id: String, val status: String) : ChatAction
    data class TagMessageSession(val id: String, val sessionId: Int) : ChatAction
    data class AddTool(val id: String, val tool: ToolBadge) : ChatAction
    data class SetToolResult(val id: String, val toolId: String, val result: String) : ChatAction
    data class AddFile(val id: String, val file: FileMeta) : ChatAction
    /** One `subagent` SSE frame — any phase (started/text/tool/tool_result/
     *  finished). [event] stays a raw [JsonObject], same looseness as the TS
     *  reducer's `Record<string, unknown>`: several delegations can be in
     *  flight at once and their frames interleave, so the shape is read inside
     *  the reducer case rather than pinned to one payload type here. */
    data class Subagent(val id: String, val event: JsonObject) : ChatAction
    data class FinishMessage(val id: String, val sessionId: Int) : ChatAction
    data class ErrorMessage(val id: String, val error: String) : ChatAction
    data class UpdateSessionTitle(val sessionId: Int, val title: String) : ChatAction
    data class DeleteMessage(val id: String) : ChatAction
    data class TruncateFrom(val id: String) : ChatAction
    data class DeleteSession(val id: Int) : ChatAction
}

/** Pure reducer — literal port of chatReducer in store/chat.ts. Fixture/tested. */
fun reduce(state: ChatState, action: ChatAction): ChatState = when (action) {
    is ChatAction.SetConfig -> state.copy(config = action.config)

    is ChatAction.SetSessions -> state.copy(sessions = action.sessions.toPersistentList())

    is ChatAction.SelectSession -> {
        // Preserve a still-streaming bubble that belongs to the selected session
        // and isn't in the loaded payload — the reattach effect and history load
        // race, and a wholesale replace would wipe the live tail (the in-flight
        // turn isn't in the DB yet).
        val kept = state.messages.filter { m ->
            m.isStreaming &&
                m.sessionId == action.sessionId &&
                action.messages.none { it.id == m.id }
        }
        state.copy(
            activeSessionId = action.sessionId,
            messages = (action.messages + kept).toPersistentList(),
            isStreaming = kept.isNotEmpty(),
        )
    }

    is ChatAction.TagMessageSession -> state.copy(
        messages = state.messages.map { m ->
            if (m.id == action.id) m.copy(sessionId = action.sessionId) else m
        }.toPersistentList(),
    )

    ChatAction.NewChat -> state.copy(activeSessionId = null, messages = persistentListOf(), isStreaming = false)

    is ChatAction.AddUserMessage -> state.copy(messages = (state.messages + action.message).toPersistentList())

    is ChatAction.AddAssistantMessage -> state.copy(
        isStreaming = true,
        messages = (state.messages + action.message).toPersistentList(),
    )

    is ChatAction.AppendChunk -> state.copy(
        messages = state.messages.map { m ->
            // First text clears any pending status line.
            if (m.id == action.id) m.copy(content = m.content + action.chunk, status = null) else m
        }.toPersistentList(),
    )

    is ChatAction.SetStatus -> state.copy(
        messages = state.messages.map { m ->
            if (m.id == action.id) m.copy(status = action.status) else m
        }.toPersistentList(),
    )

    is ChatAction.AddTool -> state.copy(
        messages = state.messages.map { m ->
            if (m.id == action.id) m.copy(tools = (m.tools + action.tool).toPersistentList()) else m
        }.toPersistentList(),
    )

    is ChatAction.SetToolResult -> state.copy(
        messages = state.messages.map { m ->
            if (m.id == action.id) {
                m.copy(
                    tools = m.tools.map { t ->
                        if (t.id == action.toolId) t.copy(result = action.result) else t
                    }.toPersistentList(),
                )
            } else {
                m
            }
        }.toPersistentList(),
    )

    is ChatAction.AddFile -> state.copy(
        messages = state.messages.map { m ->
            if (m.id == action.id) {
                m.copy(files = ((m.files ?: persistentListOf()) + action.file).toPersistentList())
            } else {
                m
            }
        }.toPersistentList(),
    )

    is ChatAction.Subagent -> {
        val e = action.event
        val runId = e["id"]?.jsonPrimitive?.contentOrNull
        if (runId == null) {
            state
        } else {
            state.copy(
                messages = state.messages.map { m ->
                    if (m.id != action.id) return@map m
                    val runs = m.subagents ?: persistentListOf()
                    val idx = runs.indexOfFirst { it.id == runId }
                    var run = if (idx >= 0) {
                        runs[idx]
                    } else {
                        SubagentRun(
                            id = runId,
                            agent = e["agent"]?.jsonPrimitive?.contentOrNull ?: "",
                            label = e["label"]?.jsonPrimitive?.contentOrNull ?: "",
                        )
                    }
                    when (e["phase"]?.jsonPrimitive?.contentOrNull) {
                        "started" -> run = run.copy(
                            prompt = e["prompt"]?.jsonPrimitive?.contentOrNull,
                            running = true,
                        )
                        "text" -> {
                            val delta = e["text"]?.jsonPrimitive?.contentOrNull
                            if (!delta.isNullOrEmpty()) {
                                val last = run.steps.lastOrNull()
                                run = if (last?.kind == "text") {
                                    run.copy(
                                        steps = run.steps.set(
                                            run.steps.lastIndex,
                                            last.copy(text = (last.text ?: "") + delta),
                                        ),
                                    )
                                } else {
                                    run.copy(steps = (run.steps + SubagentStep(kind = "text", text = delta)).toPersistentList())
                                }
                            }
                        }
                        "tool" -> run = run.copy(
                            steps = (
                                run.steps + SubagentStep(
                                    kind = "tool",
                                    tool = e["tool"]?.jsonPrimitive?.contentOrNull,
                                    input = e["input"],
                                )
                                ).toPersistentList(),
                        )
                        "tool_result" -> {
                            val i = run.steps.indexOfLast { it.kind == "tool" && it.result == null }
                            if (i >= 0) {
                                run = run.copy(
                                    steps = run.steps.set(
                                        i,
                                        run.steps[i].copy(result = e["result"]?.jsonPrimitive?.contentOrNull),
                                    ),
                                )
                            }
                        }
                        "finished" -> run = run.copy(
                            running = false,
                            ok = e["ok"]?.jsonPrimitive?.contentOrNull != "false",
                            report = e["report"]?.jsonPrimitive?.contentOrNull,
                        )
                    }
                    val nextRuns = if (idx >= 0) runs.set(idx, run) else (runs + run).toPersistentList()
                    m.copy(subagents = nextRuns)
                }.toPersistentList(),
            )
        }
    }

    is ChatAction.FinishMessage -> {
        // If the streaming message is no longer in view, the user switched away
        // mid-generation. The backend still finished + saved it, so do NOT yank
        // them back or flip the global streaming flag — just ignore.
        if (state.messages.none { it.id == action.id }) {
            state
        } else {
            state.copy(
                isStreaming = false,
                activeSessionId = action.sessionId,
                messages = state.messages.map { m ->
                    if (m.id == action.id) m.copy(isStreaming = false, status = null) else m
                }.toPersistentList(),
            )
        }
    }

    is ChatAction.ErrorMessage -> {
        if (state.messages.none { it.id == action.id }) {
            state
        } else {
            state.copy(
                isStreaming = false,
                messages = state.messages.map { m ->
                    // Preserve everything already streamed; attach the error as a
                    // SEPARATE banner. A mid-turn drop must never vaporize the answer.
                    if (m.id == action.id) {
                        m.copy(isStreaming = false, isError = true, errorNote = action.error, status = null)
                    } else {
                        m
                    }
                }.toPersistentList(),
            )
        }
    }

    is ChatAction.UpdateSessionTitle -> state.copy(
        sessions = state.sessions.map { s ->
            if (s.id == action.sessionId) s.copy(title = action.title) else s
        }.toPersistentList(),
    )

    is ChatAction.DeleteMessage -> state.copy(
        messages = state.messages.filter { it.id != action.id }.toPersistentList(),
    )

    is ChatAction.TruncateFrom -> {
        val idx = state.messages.indexOfFirst { it.id == action.id }
        if (idx == -1) state else state.copy(messages = state.messages.subList(0, idx).toPersistentList())
    }

    is ChatAction.DeleteSession -> {
        val wasActive = state.activeSessionId == action.id
        state.copy(
            sessions = state.sessions.filter { it.id != action.id }.toPersistentList(),
            activeSessionId = if (wasActive) null else state.activeSessionId,
            messages = if (wasActive) persistentListOf() else state.messages,
        )
    }
}
