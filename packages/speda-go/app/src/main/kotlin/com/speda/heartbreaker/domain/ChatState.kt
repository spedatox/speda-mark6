// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.domain

import androidx.compose.runtime.Immutable
import kotlinx.collections.immutable.PersistentList
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toPersistentList
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.decodeFromJsonElement

/** The backend connection + which agent this session targets (lib/types AppConfig). */
@Immutable
data class AppConfig(val apiBase: String, val apiKey: String, val agentId: String)

@Immutable
data class ChatState(
    val config: AppConfig? = null,
    val sessions: PersistentList<Session> = persistentListOf(),
    val activeSessionId: Int? = null,
    /** The project a NEW chat will be created in, or null for a loose chat. Set
     *  by opening a project and cleared by New chat outside one. On an EXISTING
     *  session it mirrors that session's own project, because the backend fixes
     *  a chat's project at birth and ignores the field thereafter — it is here
     *  so the header can name the workspace, and so the send path can pass it on
     *  the turn that creates the session. */
    val activeProjectId: Int? = null,
    val messages: PersistentList<ChatMessage> = persistentListOf(),
    val isStreaming: Boolean = false,
    val room: RoomInfo? = null,
)

/**
 * The 22 reducer actions — same names/semantics as store/chat.ts. Sealed so the
 * `when` in [reduce] is exhaustive.
 */
sealed interface ChatAction {
    data class SetRoom(val room: RoomInfo, val sessionId: Int) : ChatAction
    data class RoomFrame(val event: RoomEvent, val placeholderId: String, val sessionId: Int) : ChatAction
    data class FinishRoomTurn(val sessionId: Int, val error: String? = null) : ChatAction
    data class WorkerSnapshot(val sessionId: Int, val parentStep: String, val parentMessage: String?, val run: SubagentRun, val create: Boolean) : ChatAction
    data class SetConfig(val config: AppConfig) : ChatAction
    data class SetSessions(val sessions: List<Session>) : ChatAction
    /** [projectId] is passed when the caller already knows it (opening a chat
     *  from inside the projects screen, whose listing may reach us before the
     *  main session list has refreshed); otherwise it is read off the row. */
    data class SelectSession(
        val sessionId: Int,
        val messages: List<ChatMessage>,
        val projectId: Int? = null,
    ) : ChatAction

    /** Start a new chat, optionally inside a project. A null [projectId] means a
     *  LOOSE chat — never "keep the current one", so leaving a project cannot
     *  leak its standing instructions into the next unrelated conversation. */
    data class NewChat(val projectId: Int? = null) : ChatAction
    data class AddUserMessage(val message: ChatMessage) : ChatAction
    data class AddAssistantMessage(val message: ChatMessage) : ChatAction
    data class AppendChunk(val id: String, val chunk: String) : ChatAction
    data class AppendThinking(val id: String, val text: String) : ChatAction
    data class ThinkingRedacted(val id: String) : ChatAction
    /** Fired once, the moment the turn moves past thinking (first visible
     *  content, a tool call, or the turn finishing) — collapses the panel. */
    data class ThinkingDone(val id: String, val ms: Long) : ChatAction
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
    is ChatAction.SetRoom -> if (state.activeSessionId != action.sessionId) state else state.copy(room = updateRoom(state.room, action.room))
    is ChatAction.RoomFrame -> if (state.activeSessionId != action.sessionId) state else {
        val snapshot = action.event.payload["room"]?.takeUnless { it is kotlinx.serialization.json.JsonNull }?.let { roomJson.decodeFromJsonElement<RoomInfo>(it) }
        state.copy(room = snapshot?.let { updateRoom(state.room, it) } ?: state.room,
            messages = consolidateWorkerAnchors(foldRoomMessages(state.messages, action.event, action.placeholderId, action.sessionId)).toPersistentList())
    }
    is ChatAction.FinishRoomTurn -> if (state.activeSessionId != action.sessionId) state else state.copy(isStreaming = false,
        messages = state.messages.map { message -> if (!message.isStreaming) message else message.copy(isStreaming = false,
            status = null, thinkingDone = true, isError = action.error != null, errorNote = action.error) }.toPersistentList())
    is ChatAction.WorkerSnapshot -> if (state.activeSessionId != action.sessionId) state else state.copy(
        messages = upsertWorkerMessage(state.messages, action.parentStep, action.parentMessage, action.sessionId, action.run, action.create).toPersistentList())
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
        val current = if (state.activeSessionId == action.sessionId) state.messages else emptyList()
        val history = mergeWorkerHistory(mergeRoomHistory(action.messages, current), current)
        val tails = kept.filter { tail -> history.none { it.coordinationStepId != null && it.coordinationStepId == tail.coordinationStepId } }
        state.copy(
            activeSessionId = action.sessionId,
            // Follow the opened chat into (or out of) its project, so the header
            // always names the workspace the transcript was written in rather
            // than whichever one happened to be open a moment ago.
            activeProjectId = action.projectId
                ?: state.sessions.firstOrNull { it.id == action.sessionId }?.projectId,
            room = if (state.activeSessionId == action.sessionId) state.room else null,
            messages = consolidateWorkerAnchors(history + tails).toPersistentList(),
            isStreaming = kept.isNotEmpty() || state.activeSessionId == action.sessionId && state.isStreaming,
        )
    }

    is ChatAction.TagMessageSession -> state.copy(
        activeSessionId = state.activeSessionId ?: action.sessionId,
        messages = state.messages.updateMessage(action.id) { m ->
            if (m.id == action.id) m.copy(sessionId = action.sessionId) else m
        },
    )

    is ChatAction.NewChat -> state.copy(
        room = null,
        activeSessionId = null,
        activeProjectId = action.projectId,
        messages = persistentListOf(),
        isStreaming = false,
    )

    is ChatAction.AddUserMessage -> state.copy(messages = state.messages.add(action.message))

    is ChatAction.AddAssistantMessage -> state.copy(
        isStreaming = true,
        messages = state.messages.add(action.message),
    )

    is ChatAction.AppendChunk -> state.copy(
        messages = state.messages.updateMessage(action.id) { m ->
            // First text clears any pending status line.
            if (m.id == action.id) m.copy(content = m.content + action.chunk, status = null) else m
        },
    )

    is ChatAction.AppendThinking -> state.copy(
        messages = state.messages.updateMessage(action.id) { m ->
            if (m.id == action.id) m.copy(thinking = (m.thinking ?: "") + action.text) else m
        },
    )

    is ChatAction.ThinkingRedacted -> state.copy(
        messages = state.messages.updateMessage(action.id) { m ->
            if (m.id == action.id) m.copy(thinkingRedacted = true) else m
        },
    )

    is ChatAction.ThinkingDone -> state.copy(
        messages = state.messages.updateMessage(action.id) { m ->
            if (m.id == action.id && !m.thinkingDone) m.copy(thinkingDone = true, thinkingMs = action.ms) else m
        },
    )

    is ChatAction.SetStatus -> state.copy(
        messages = state.messages.updateMessage(action.id) { m ->
            if (m.id == action.id) m.copy(status = action.status) else m
        },
    )

    is ChatAction.AddTool -> state.copy(
        messages = state.messages.updateMessage(action.id) { m ->
            if (m.id == action.id) m.copy(tools = (m.tools + action.tool).toPersistentList()) else m
        },
    )

    is ChatAction.SetToolResult -> state.copy(
        messages = state.messages.updateMessage(action.id) { m ->
            if (m.id == action.id) {
                m.copy(
                    tools = m.tools.map { t ->
                        if (t.id == action.toolId) t.copy(result = action.result) else t
                    }.toPersistentList(),
                )
            } else {
                m
            }
        },
    )

    is ChatAction.AddFile -> state.copy(
        messages = state.messages.updateMessage(action.id) { m ->
            if (m.id == action.id) {
                m.copy(files = ((m.files ?: persistentListOf()) + action.file).toPersistentList())
            } else {
                m
            }
        },
    )

    is ChatAction.Subagent -> {
        val runId = action.event["id"]?.jsonPrimitive?.contentOrNull
        if (runId == null) state else state.copy(messages = state.messages.updateMessage(action.id) { message ->
            val runs = message.subagents ?: persistentListOf()
            val index = runs.indexOfFirst { it.id == runId }
            val next = foldSubagentEvent(if (index >= 0) runs[index] else null, action.event)
            message.copy(subagents = if (index >= 0) runs.set(index, next) else (runs + next).toPersistentList())
        })
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
                activeSessionId = if (action.sessionId != 0) action.sessionId else state.activeSessionId,
                messages = state.messages.updateMessage(action.id) { m ->
                    if (m.id == action.id) m.copy(isStreaming = false, status = null) else m
                },
            )
        }
    }

    is ChatAction.ErrorMessage -> {
        if (state.messages.none { it.id == action.id }) {
            state
        } else {
            state.copy(
                isStreaming = false,
                messages = state.messages.updateMessage(action.id) { m ->
                    // Preserve everything already streamed; attach the error as a
                    // SEPARATE banner. A mid-turn drop must never vaporize the answer.
                    if (m.id == action.id) {
                        m.copy(isStreaming = false, isError = true, errorNote = action.error, status = null)
                    } else {
                        m
                    }
                },
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

private val roomJson = Json { ignoreUnknownKeys = true }

/** Tail updates share the persistent list's untouched nodes instead of copying
 * every historical row on every token. Search from the tail for live replies. */
private inline fun PersistentList<ChatMessage>.updateMessage(
    id: String,
    transform: (ChatMessage) -> ChatMessage,
): PersistentList<ChatMessage> {
    val index = indexOfLast { it.id == id }
    if (index < 0) return this
    val next = transform(this[index])
    return if (next == this[index]) this else set(index, next)
}
