package com.speda.heartbreaker.data

import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.ChatMessage
import com.speda.heartbreaker.domain.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

/**
 * The backend transport — a Kotlin port of lib/api.ts. Streaming (chat/attach)
 * reads the SSE body line-by-line by hand for byte-level parity with the web
 * (§4.1). Everything else is a plain suspend function.
 *
 * [streamClient] has readTimeout/callTimeout = 0 (streams idle during long tool
 * runs; the watchdog owns liveness). [restClient] uses ordinary timeouts.
 */
class IgorApi(
    private val streamClient: OkHttpClient,
    private val restClient: OkHttpClient,
) {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    /** Options for a chat turn (lib/api StreamOpts). */
    data class StreamOpts(
        val model: String? = null,
        val systemPrompt: String? = null,
        val images: List<ImageBlock> = emptyList(),
        val documents: List<DocBlock> = emptyList(),
        val keepMessages: Int? = null,
        val regenerate: Boolean = false,
        val cwd: String? = null,
    )

    // ── Streaming ────────────────────────────────────────────────────────────

    fun streamChat(
        message: String,
        sessionId: Int?,
        config: AppConfig,
        opts: StreamOpts = StreamOpts(),
    ): Flow<SseEvent> {
        val body = buildJsonObject {
            put("message", message)
            put("session_id", sessionId) // Int? → JsonNull when null
            opts.model?.let { put("model", it) }
            opts.systemPrompt?.let { put("system_prompt", it) }
            // Only send the keys that have content — the web omits them entirely.
            if (opts.images.isNotEmpty()) {
                put(
                    "attachments",
                    buildJsonArray {
                        opts.images.forEach { img ->
                            add(
                                buildJsonObject {
                                    put("media_type", img.mediaType)
                                    put("data", img.data)
                                },
                            )
                        }
                    },
                )
            }
            if (opts.documents.isNotEmpty()) {
                put(
                    "documents",
                    buildJsonArray {
                        opts.documents.forEach { doc ->
                            add(
                                buildJsonObject {
                                    put("name", doc.name)
                                    put("media_type", doc.mediaType)
                                    put("data", doc.data)
                                    put("size", doc.size)
                                },
                            )
                        }
                    },
                )
            }
            opts.keepMessages?.let { put("keep_messages", it) }
            if (opts.regenerate) put("regenerate", true)
            opts.cwd?.let { put("cwd", it) }
        }
        val request = Request.Builder()
            .url("${config.apiBase}/chat/${config.agentId}")
            .header("X-API-Key", config.apiKey)
            .post(body.toString().toRequestBody(jsonMedia))
            .build()
        return streamSse(request)
    }

    fun attachStream(config: AppConfig, requestId: String): Flow<SseEvent> {
        val request = Request.Builder()
            .url("${config.apiBase}/chat/attach/$requestId")
            .header("X-API-Key", config.apiKey)
            .get()
            .build()
        return streamSse(request)
    }

    /** Shared SSE reader. The blocking read runs in a child coroutine so
     *  [awaitClose] can cancel the Call the instant the collector cancels. */
    private fun streamSse(request: Request): Flow<SseEvent> = channelFlow {
        val call = streamClient.newCall(request)
        launch(Dispatchers.IO) {
            call.execute().use { response ->
                if (!response.isSuccessful) {
                    val text = runCatching { response.body?.string() }.getOrNull().orEmpty()
                    throw IOException(if (text.isNotBlank()) text.take(300) else "HTTP ${response.code}")
                }
                val source = response.body?.source() ?: return@use
                while (isActive) {
                    val line = source.readUtf8Line() ?: break
                    if (!line.startsWith("data: ")) continue
                    val raw = line.substring(6).trim()
                    if (raw.isEmpty()) continue
                    runCatching { json.decodeFromString(SseEvent.serializer(), raw) }
                        .getOrNull()?.let { send(it) }
                }
            }
            close()
        }
        awaitClose { call.cancel() }
    }.flowOn(Dispatchers.IO)

    // ── Detached-run coordination ─────────────────────────────────────────────

    suspend fun fetchActiveRuns(config: AppConfig, sessionId: Int? = null): List<ActiveRun> = withContext(Dispatchers.IO) {
        val q = if (sessionId != null) "?session_id=$sessionId" else ""
        runCatching {
            getString(config, "/chat/active$q")?.let { json.decodeFromString<List<ActiveRun>>(it) }
        }.getOrNull() ?: emptyList()
    }

    suspend fun cancelRun(config: AppConfig, requestId: String): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            val request = Request.Builder()
                .url("${config.apiBase}/chat/cancel/$requestId")
                .header("X-API-Key", config.apiKey)
                .post(ByteArray(0).toRequestBody(null))
                .build()
            restClient.newCall(request).execute().use { res ->
                if (!res.isSuccessful) return@use false
                val obj = json.parseToJsonElement(res.body?.string().orEmpty()).jsonObject
                obj["cancelled"]?.jsonPrimitive?.booleanOrNull ?: false
            }
        }.getOrDefault(false)
    }

    // ── Sessions / messages / welcome ─────────────────────────────────────────

    suspend fun fetchSessions(config: AppConfig, limit: Int = 500): List<Session> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/sessions?agent_id=${config.agentId}&limit=$limit")?.let { body ->
                json.decodeFromString<List<SessionDto>>(body).map { Session(it.id, it.title, it.startedAt) }
            }
        }.getOrNull() ?: emptyList()
    }

    suspend fun fetchMessages(config: AppConfig, sessionId: Int): List<ChatMessage> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/sessions/$sessionId/messages")?.let { body ->
                MessageJson.parseArray(json.parseToJsonElement(body) as JsonArray)
            }
        }.getOrNull() ?: emptyList()
    }

    suspend fun fetchModels(config: AppConfig): List<ModelInfo> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/models")?.let { json.decodeFromString<List<ModelInfo>>(it) }
        }.getOrNull() ?: emptyList()
    }

    suspend fun fetchWelcome(config: AppConfig, agentId: String): String = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/welcome/$agentId")?.let {
                json.parseToJsonElement(it).jsonObject["text"]?.jsonPrimitive?.contentOrNull
            }
        }.getOrNull().orEmpty()
    }

    // ── helpers ────────────────────────────────────────────────────────────────

    private fun getString(config: AppConfig, path: String): String? {
        val request = Request.Builder()
            .url("${config.apiBase}$path")
            .header("X-API-Key", config.apiKey)
            .get()
            .build()
        restClient.newCall(request).execute().use { res ->
            if (!res.isSuccessful) return null
            return res.body?.string()
        }
    }

    @Serializable
    private data class SessionDto(
        val id: Int,
        val title: String? = null,
        @SerialName("started_at") val startedAt: String = "",
    )
}
