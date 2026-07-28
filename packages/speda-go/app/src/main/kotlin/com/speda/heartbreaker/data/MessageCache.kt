package com.speda.heartbreaker.data

import com.speda.heartbreaker.domain.ChatMessage
import com.speda.heartbreaker.domain.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
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

    // ── Session list ─────────────────────────────────────────────────────────
    // Cached too, otherwise an offline launch shows "// NO SESSIONS" and there's
    // no way to reach the transcripts that ARE on disk.

    private fun sessionsFile(agentId: String) = File(dir, "sessions_$agentId.json")

    suspend fun saveSessions(agentId: String, sessions: List<Session>) = withContext(Dispatchers.IO) {
        if (agentId.isEmpty() || sessions.isEmpty()) return@withContext
        runCatching {
            val array = buildJsonArray {
                sessions.forEach { s ->
                    add(
                        buildJsonObject {
                            put("id", s.id)
                            s.title?.let { put("title", it) }
                            put("started_at", s.startedAt)
                        },
                    )
                }
            }
            sessionsFile(agentId).writeText(array.toString())
        }
    }

    suspend fun loadSessions(agentId: String): List<Session> = withContext(Dispatchers.IO) {
        if (agentId.isEmpty()) return@withContext emptyList()
        runCatching {
            val f = sessionsFile(agentId)
            if (!f.exists()) return@runCatching emptyList()
            (json.parseToJsonElement(f.readText()) as JsonArray).mapNotNull { el ->
                val o = el as? JsonObject ?: return@mapNotNull null
                Session(
                    id = o["id"]?.jsonPrimitive?.int ?: return@mapNotNull null,
                    title = o["title"]?.jsonPrimitive?.content,
                    startedAt = o["started_at"]?.jsonPrimitive?.content.orEmpty(),
                )
            }
        }.getOrDefault(emptyList())
    }
}
