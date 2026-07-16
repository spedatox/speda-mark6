package com.speda.heartbreaker.data

import com.speda.heartbreaker.domain.ChatMessage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import java.io.File

/**
 * Offline transcript mirror — a per-(agent, session) JSON file in the app's cache
 * dir. Port of store/messageCache.ts. The backend is the source of truth; this is
 * the offline/failure fallback: snapshot each session's messages as turns settle,
 * hydrate from it when the server can't be reached. A successful fetch always
 * wins and refreshes the snapshot (server-wins-unless-empty — enforced by the
 * caller). Live flags are dropped on save so a reload never rehydrates a spinning
 * bubble (handled in MessageJson.encodeMessage).
 */
class MessageCache(cacheRoot: File) {

    private val dir: File = File(cacheRoot, "transcripts").apply { mkdirs() }
    private val json = Json { ignoreUnknownKeys = true }

    private fun file(agentId: String, sessionId: Int) = File(dir, "msgs_${agentId}_$sessionId.json")

    suspend fun save(agentId: String, sessionId: Int, messages: List<ChatMessage>) = withContext(Dispatchers.IO) {
        if (agentId.isEmpty()) return@withContext
        runCatching {
            file(agentId, sessionId).writeText(MessageJson.encodeArray(messages).toString())
        }
    }

    suspend fun load(agentId: String, sessionId: Int): List<ChatMessage>? = withContext(Dispatchers.IO) {
        if (agentId.isEmpty()) return@withContext null
        runCatching {
            val f = file(agentId, sessionId)
            if (!f.exists()) return@runCatching null
            val parsed = MessageJson.parseArray(json.parseToJsonElement(f.readText()) as JsonArray)
            parsed.ifEmpty { null }
        }.getOrNull()
    }
}
