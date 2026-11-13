package com.speda.heartbreaker.domain

import com.speda.heartbreaker.data.MessageJson
import kotlinx.collections.immutable.toPersistentList
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.*

@Serializable
data class RoomInfo(
    val id: String,
    val title: String = "",
    val lead: String,
    val members: List<String>,
    val version: Int,
    val sequence: Long,
    @SerialName("active_run") val activeRun: String? = null,
)

@Serializable
data class RoomEvent(
    @SerialName("room_id") val roomId: String,
    val sequence: Long,
    @SerialName("run_id") val runId: String? = null,
    @SerialName("step_id") val stepId: String? = null,
    val speaker: String = "",
    val kind: String,
    val payload: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class RoomPage(val room: RoomInfo, val events: List<RoomEvent>, @SerialName("next_cursor") val nextCursor: Long)

fun canonicalRoomAgent(id: String) = if (id == "nightcrawler") "friday" else id
fun JsonObject.roomText(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull

fun updateRoom(current: RoomInfo?, next: RoomInfo): RoomInfo =
    if (current?.id == next.id && (current.version > next.version ||
            current.version == next.version && current.sequence > next.sequence)) current else next

fun historyMessageCount(messages: List<ChatMessage>) = messages.count {
    it.roomNotice == null && !it.isWorkerAnchor && (!it.id.startsWith("coord:") || it.serverMessageId != null)
}

fun mergeRoomHistory(loaded: List<ChatMessage>, current: List<ChatMessage>): List<ChatMessage> = loaded.map { incoming ->
    val live = current.firstOrNull { it.id == incoming.id || !it.isWorkerAnchor && it.coordinationStepId != null && it.coordinationStepId == incoming.coordinationStepId }
    if (live != null && live.isStreaming && live.roomEventSequence > maxOf(incoming.roomEventSequence, incoming.roomSequence ?: 0)) live else incoming
}

private fun orderedRoomRows(messages: List<ChatMessage>): List<ChatMessage> {
    val ordered = messages.filter { it.roomOrder != null }.sortedBy { it.roomOrder }.iterator()
    return messages.map { if (it.roomOrder != null) ordered.next() else it }
}

/** Public room-v1 frames only. Per-step sequence fences keep replay from erasing newer history. */
fun foldRoomMessages(messages: List<ChatMessage>, event: RoomEvent, placeholderId: String, sessionId: Int): List<ChatMessage> {
    val p = event.payload
    if (event.kind == "membership") {
        val id = "room:${event.roomId}:${event.sequence}"
        if (messages.any { it.id == id }) return messages
        return orderedRoomRows(messages + ChatMessage(id, Role.Assistant, "", roomNotice = p,
            roomSequence = event.sequence, roomOrder = event.sequence, sessionId = sessionId))
    }
    if (event.kind == "user_message") {
        val serverId = (p["message_id"] as? JsonPrimitive)?.intOrNull ?: return messages
        var index = messages.indexOfFirst { it.role == Role.User && (it.serverMessageId == serverId || it.id == "$serverId") }
        if (index < 0) index = messages.indexOfLast { it.role == Role.User && it.serverMessageId == null &&
            it.id.toIntOrNull() == null && it.content == p.roomText("text") }
        if ((p["replay"] as? JsonPrimitive)?.booleanOrNull == true && index >= 0) return messages
        val row = (messages.getOrNull(index) ?: ChatMessage("$serverId", Role.User, "")).copy(
            content = p.roomText("text").orEmpty(), serverMessageId = serverId, roomSequence = event.sequence,
            roomOrder = event.sequence, sessionId = sessionId)
        return orderedRoomRows(if (index < 0) messages + row else messages.mapIndexed { at, m -> if (at == index) row else m })
    }
    val step = event.stepId ?: return messages
    val index = messages.indexOfFirst { it.coordinationStepId == step && !it.isWorkerAnchor }
    val existing = messages.getOrNull(index)
    if (existing != null && event.sequence <= maxOf(existing.roomEventSequence, existing.roomSequence ?: 0))
        return if (event.kind == "speaker_started") messages.filter { it.id != placeholderId } else messages
    if (event.kind == "speaker_started") {
        if (p.roomText("mode") == "consult") return messages
        val row = (existing ?: ChatMessage("coord:$step", Role.Assistant, "")).copy(
            id = "coord:$step", speaker = event.speaker, coordinationStepId = step, sessionId = sessionId,
            content = "", tools = kotlinx.collections.immutable.persistentListOf(), files = null, thinking = null,
            thinkingDone = false, thinkingRedacted = false, isStreaming = true, isError = false, errorNote = null,
            roomSequence = event.sequence, roomOrder = event.sequence, roomEventSequence = event.sequence)
        val rows = if (index < 0) messages + row else messages.mapIndexed { at, m -> if (at == index) row else m }
        return orderedRoomRows(rows.filter { it.id != placeholderId })
    }
    if (existing == null && event.kind == "step_waiting") return messages + ChatMessage(
        id = "coord:$step", role = Role.Assistant, content = p.roomText("text").orEmpty(),
        speaker = event.speaker, coordinationStepId = step, sessionId = sessionId,
        isStreaming = false, status = "Command running", roomSequence = event.sequence, roomOrder = event.sequence,
        roomEventSequence = event.sequence)
    if (existing == null) return messages
    val data = p["data"] ?: JsonNull
    val next = when (event.kind) {
        "step_waiting" -> existing.copy(isStreaming = false, status = "Command running")
        "operation_finished" -> existing.copy(status = "Command ${p.roomText("status") ?: "finished"}")
        "step_resumed" -> existing.copy(isStreaming = true, status = null)
        "delta" -> existing.copy(content = existing.content + p.roomText("text").orEmpty(), status = null)
        "message" -> existing.copy(content = p.roomText("text").orEmpty(), isStreaming = false,
            serverMessageId = (p["message_id"] as? JsonPrimitive)?.intOrNull, roomSequence = event.sequence,
            status = null, thinkingDone = true)
        "thinking" -> existing.copy(thinking = existing.thinking.orEmpty() + (data as? JsonObject)?.roomText("text").orEmpty(),
            thinkingRedacted = existing.thinkingRedacted || ((data as? JsonObject)?.get("redacted") as? JsonPrimitive)?.booleanOrNull == true)
        "tool" -> existing.copy(thinkingDone = true, tools = MessageJson.toolFrom(data)?.let {
            (existing.tools + it.copy(afterChars = existing.content.length)).toPersistentList()
        } ?: existing.tools)
        "tool_result" -> existing.copy(tools = existing.tools.map { tool ->
            val result = data as? JsonObject
            if (tool.id == result?.roomText("id")) tool.copy(result = result.roomText("result")) else tool
        }.toPersistentList())
        "file" -> existing.copy(files = MessageJson.fileFrom(data)?.let {
            (existing.files.orEmpty() + it).toPersistentList()
        } ?: existing.files)
        "contribution_stopped", "contribution_skipped" -> existing.copy(isStreaming = false, isError = true,
            thinkingDone = true, errorNote = p.roomText("reason") ?: "Contribution stopped", status = null)
        else -> return messages
    }.copy(roomEventSequence = event.sequence)
    return messages.mapIndexed { at, m -> if (at == index) next else m }
}
