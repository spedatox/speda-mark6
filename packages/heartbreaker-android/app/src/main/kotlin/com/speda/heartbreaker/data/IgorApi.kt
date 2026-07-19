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
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
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
        /** Ambient platform + (opt-in) location context for this turn. */
        val clientContext: ClientContext? = null,
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
            opts.clientContext?.let { cc ->
                put(
                    "client_context",
                    buildJsonObject {
                        put("platform", cc.platform)
                        put("device", cc.device)
                        put("os_version", cc.osVersion)
                        put("app_version", cc.appVersion)
                        put("locale", cc.locale)
                        cc.location?.let { loc ->
                            put(
                                "location",
                                buildJsonObject {
                                    put("lat", loc.lat)
                                    put("lng", loc.lng)
                                    loc.accuracyM?.let { put("accuracy_m", it) }
                                    loc.place?.let { put("place", it) }
                                },
                            )
                        }
                    },
                )
            }
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

    /** Rename a session (PATCH /sessions/{id} {title}). Returns success. */
    suspend fun renameSession(config: AppConfig, sessionId: Int, title: String): Boolean =
        withContext(Dispatchers.IO) {
            runCatching {
                patchJson(config, "/sessions/$sessionId", buildJsonObject { put("title", title) }) != null
            }.getOrDefault(false)
        }

    /** Delete a session and its messages (DELETE /sessions/{id}). Returns success. */
    suspend fun deleteSession(config: AppConfig, sessionId: Int): Boolean =
        withContext(Dispatchers.IO) {
            runCatching { deleteRequest(config, "/sessions/$sessionId") != null }.getOrDefault(false)
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

    // ── Budget mode (GET/POST /budget-mode) ────────────────────────────────────

    suspend fun getBudgetMode(config: AppConfig): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/budget-mode")?.let {
                json.parseToJsonElement(it).jsonObject["budget_mode"]?.jsonPrimitive?.booleanOrNull
            }
        }.getOrNull() ?: true
    }

    suspend fun setBudgetMode(config: AppConfig, enabled: Boolean): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            postJson(config, "/budget-mode", buildJsonObject { put("enabled", enabled) })?.let {
                json.parseToJsonElement(it).jsonObject["budget_mode"]?.jsonPrimitive?.booleanOrNull
            }
        }.getOrNull() ?: enabled
    }

    // ── Connections / toolsets (GET/POST /connections, OAuth) ───────────────────

    suspend fun getConnections(config: AppConfig): ConnectionsResult = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/connections")?.let { json.decodeFromString<ConnectionsResult>(it) }
        }.getOrNull() ?: ConnectionsResult()
    }

    suspend fun setConnection(config: AppConfig, server: String, active: Boolean) {
        withContext(Dispatchers.IO) {
            runCatching {
                postJson(config, "/connections", buildJsonObject { put("server", server); put("active", active) })
            }
            Unit
        }
    }

    suspend fun oauthStatus(config: AppConfig, provider: String): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/connections/$provider/status")?.let {
                json.parseToJsonElement(it).jsonObject["connected"]?.jsonPrimitive?.booleanOrNull
            }
        }.getOrNull() ?: false
    }

    suspend fun oauthLoginUrl(config: AppConfig, provider: String): String? = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/connections/$provider/login")?.let {
                json.parseToJsonElement(it).jsonObject["auth_url"]?.jsonPrimitive?.contentOrNull
            }
        }.getOrNull()
    }

    suspend fun oauthDisconnect(config: AppConfig, provider: String) {
        withContext(Dispatchers.IO) {
            runCatching { postEmpty(config, "/connections/$provider/disconnect") }
            Unit
        }
    }

    // ── Backend configuration (GET/PUT /config, /memory/sources) ────────────────

    suspend fun getConfig(config: AppConfig): List<ConfigGroupInfo> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/config")?.let { json.decodeFromString<ConfigGroupsDto>(it).groups }
        }.getOrNull() ?: emptyList()
    }

    suspend fun saveConfig(config: AppConfig, values: Map<String, JsonElement>): ConfigSaveResult = withContext(Dispatchers.IO) {
        runCatching {
            val body = buildJsonObject { put("values", JsonObject(values)) }
            putJson(config, "/config", body)?.let { json.decodeFromString<ConfigSaveResult>(it) }
        }.getOrNull() ?: ConfigSaveResult(rejected = listOf("Save failed — the backend didn't accept the change."))
    }

    suspend fun getMemorySources(config: AppConfig): MemorySources = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/memory/sources")?.let { json.decodeFromString<MemorySources>(it) }
        }.getOrNull() ?: MemorySources()
    }

    suspend fun setMemorySource(config: AppConfig, agentId: String, path: String?) {
        withContext(Dispatchers.IO) {
            runCatching {
                putJson(config, "/memory/sources", buildJsonObject { put("agent_id", agentId); put("path", path) })
            }
            Unit
        }
    }

    // ── Automations (n8n watchers + Telegram) ──────────────────────────────────

    suspend fun getAutomations(config: AppConfig): List<AutomationInfo> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/automations")?.let { json.decodeFromString<AutomationsDto>(it).automations }
        }.getOrNull() ?: emptyList()
    }

    suspend fun getAutomationsStatus(config: AppConfig): AutomationsStatus? = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/automations/status")?.let { json.decodeFromString<AutomationsStatus>(it) }
        }.getOrNull()
    }

    suspend fun toggleAutomation(config: AppConfig, id: Int, active: Boolean) {
        withContext(Dispatchers.IO) {
            runCatching { postJson(config, "/automations/$id/toggle", buildJsonObject { put("active", active) }) }
            Unit
        }
    }

    suspend fun deleteAutomation(config: AppConfig, id: Int) {
        withContext(Dispatchers.IO) {
            runCatching { deleteRequest(config, "/automations/$id") }
            Unit
        }
    }

    suspend fun telegramConnect(config: AppConfig): String? = withContext(Dispatchers.IO) {
        runCatching {
            postEmpty(config, "/automations/telegram/connect")?.let {
                json.parseToJsonElement(it).jsonObject["link"]?.jsonPrimitive?.contentOrNull
            }
        }.getOrNull()
    }

    suspend fun telegramConnected(config: AppConfig): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/automations/telegram/status")?.let {
                json.parseToJsonElement(it).jsonObject["connected"]?.jsonPrimitive?.booleanOrNull
            }
        }.getOrNull() ?: false
    }

    // ── Inter-agent comms (GET /agents/comms, House Party GET/POST) ─────────────

    /** Recent inter-agent traffic, newest first. after_id polls incrementally. */
    suspend fun fetchAgentComms(config: AppConfig, limit: Int = 100, afterId: Int = 0): List<AgentCommEntry> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/agents/comms?limit=$limit&after_id=$afterId")?.let {
                json.decodeFromString<List<AgentCommEntry>>(it)
            }
        }.getOrNull() ?: emptyList()
    }

    suspend fun getHouseParty(config: AppConfig): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/agents/house-party")?.let {
                json.parseToJsonElement(it).jsonObject["engaged"]?.jsonPrimitive?.booleanOrNull
            }
        }.getOrNull() ?: false
    }

    /** UI-side control is STAND DOWN only (engaged=false) — engaging is voice-only,
     *  passphrase-gated through SPEDA in chat. */
    suspend fun setHouseParty(config: AppConfig, engaged: Boolean): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            postJson(config, "/agents/house-party", buildJsonObject { put("engaged", engaged) })?.let {
                json.parseToJsonElement(it).jsonObject["engaged"]?.jsonPrimitive?.booleanOrNull
            }
        }.getOrNull() ?: engaged
    }

    // ── Online external peers (the Forge link) ───────────────────────────────────

    suspend fun fetchOnlineAgents(config: AppConfig): List<OnlineAgent> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/agents")?.let { json.decodeFromString<List<OnlineAgent>>(it) }
        }.getOrNull() ?: emptyList()
    }

    // ── Per-agent model routing (GET/POST /agents/models) ────────────────────────

    suspend fun fetchAgentModels(config: AppConfig): List<AgentModelInfo> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/agents/models")?.let { json.decodeFromString<List<AgentModelInfo>>(it) }
        }.getOrNull() ?: emptyList()
    }

    /** Pin an agent to a model ref; null clears the pin (back to profile policy). */
    suspend fun pinAgentModel(config: AppConfig, agentId: String, model: String?): List<AgentModelInfo> = withContext(Dispatchers.IO) {
        runCatching {
            val body = buildJsonObject { put("agent_id", agentId); put("model", model) }
            postJson(config, "/agents/models", body)?.let { json.decodeFromString<List<AgentModelInfo>>(it) }
        }.getOrNull() ?: emptyList()
    }

    // ── Knowledge bank / source-of-truth memory files (GET/PUT /memory/files) ────

    suspend fun fetchMemoryFiles(config: AppConfig): List<MemoryFileInfo> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/memory/files")?.let { json.decodeFromString<List<MemoryFileInfo>>(it) }
        }.getOrNull() ?: emptyList()
    }

    /** Commit an owner edit. On a 409 (an agent wrote since the board loaded it)
     *  returns [MemoryCommitResult.Conflict] with the fresh file so the caller can
     *  reload instead of clobbering. */
    suspend fun commitMemoryFile(
        config: AppConfig,
        path: String,
        content: String,
        expectedUpdatedAt: String?,
    ): MemoryCommitResult = withContext(Dispatchers.IO) {
        runCatching {
            val body = buildJsonObject {
                put("path", path)
                put("content", content)
                put("expected_updated_at", expectedUpdatedAt)
            }
            val request = Request.Builder()
                .url("${config.apiBase}/memory/files")
                .header("X-API-Key", config.apiKey)
                .put(body.toString().toRequestBody(jsonMedia))
                .build()
            restClient.newCall(request).execute().use { res ->
                val text = res.body?.string().orEmpty()
                when {
                    res.code == 409 -> {
                        val current = runCatching {
                            val detail = json.parseToJsonElement(text).jsonObject["detail"]?.jsonObject
                            detail?.get("current")?.let { json.decodeFromJsonElement<MemoryFileInfo>(it) }
                        }.getOrNull()
                        MemoryCommitResult.Conflict(current)
                    }
                    res.isSuccessful -> MemoryCommitResult.Ok(json.decodeFromString(text))
                    else -> MemoryCommitResult.Failed
                }
            }
        }.getOrDefault(MemoryCommitResult.Failed)
    }

    suspend fun fetchMemoryRevisions(config: AppConfig, path: String): List<MemoryRevisionInfo> = withContext(Dispatchers.IO) {
        runCatching {
            val q = java.net.URLEncoder.encode(path, "UTF-8")
            getString(config, "/memory/files/revisions?path=$q")?.let { json.decodeFromString<List<MemoryRevisionInfo>>(it) }
        }.getOrNull() ?: emptyList()
    }

    suspend fun restoreMemoryRevision(config: AppConfig, revisionId: Int): MemoryFileInfo? = withContext(Dispatchers.IO) {
        runCatching {
            postJson(config, "/memory/files/restore", buildJsonObject { put("revision_id", revisionId) })?.let {
                json.decodeFromString<MemoryFileInfo>(it)
            }
        }.getOrNull()
    }

    // ── Data (import chats, index history) ──────────────────────────────────────

    suspend fun importChats(config: AppConfig, fileName: String, bytes: ByteArray): String = withContext(Dispatchers.IO) {
        runCatching {
            val part = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart("file", fileName, bytes.toRequestBody("application/zip".toMediaType()))
                .build()
            val request = Request.Builder()
                .url("${config.apiBase}/admin/import-chats")
                .header("X-API-Key", config.apiKey)
                .post(part)
                .build()
            restClient.newCall(request).execute().use { res ->
                val body = res.body?.string().orEmpty()
                if (!res.isSuccessful) return@use "Import failed (HTTP ${res.code})."
                json.parseToJsonElement(body).jsonObject["message"]?.jsonPrimitive?.contentOrNull
                    ?: "Import started in the background."
            }
        }.getOrElse { "Import failed: ${it.message}" }
    }

    suspend fun indexHistory(config: AppConfig): String = withContext(Dispatchers.IO) {
        runCatching {
            postEmpty(config, "/admin/index-history")?.let {
                json.parseToJsonElement(it).jsonObject["message"]?.jsonPrimitive?.contentOrNull
            } ?: "Indexing started in the background."
        }.getOrElse { "Indexing failed: ${it.message}" }
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

    private fun postJson(config: AppConfig, path: String, body: JsonObject): String? {
        val request = Request.Builder()
            .url("${config.apiBase}$path")
            .header("X-API-Key", config.apiKey)
            .post(body.toString().toRequestBody(jsonMedia))
            .build()
        restClient.newCall(request).execute().use { res ->
            if (!res.isSuccessful) return null
            return res.body?.string()
        }
    }

    private fun postEmpty(config: AppConfig, path: String): String? {
        val request = Request.Builder()
            .url("${config.apiBase}$path")
            .header("X-API-Key", config.apiKey)
            .post(ByteArray(0).toRequestBody(null))
            .build()
        restClient.newCall(request).execute().use { res ->
            if (!res.isSuccessful) return null
            return res.body?.string()
        }
    }

    private fun putJson(config: AppConfig, path: String, body: JsonObject): String? {
        val request = Request.Builder()
            .url("${config.apiBase}$path")
            .header("X-API-Key", config.apiKey)
            .put(body.toString().toRequestBody(jsonMedia))
            .build()
        restClient.newCall(request).execute().use { res ->
            if (!res.isSuccessful) return null
            return res.body?.string()
        }
    }

    private fun patchJson(config: AppConfig, path: String, body: JsonObject): String? {
        val request = Request.Builder()
            .url("${config.apiBase}$path")
            .header("X-API-Key", config.apiKey)
            .patch(body.toString().toRequestBody(jsonMedia))
            .build()
        restClient.newCall(request).execute().use { res ->
            if (!res.isSuccessful) return null
            return res.body?.string()
        }
    }

    private fun deleteRequest(config: AppConfig, path: String): String? {
        val request = Request.Builder()
            .url("${config.apiBase}$path")
            .header("X-API-Key", config.apiKey)
            .delete()
            .build()
        restClient.newCall(request).execute().use { res ->
            if (!res.isSuccessful) return null
            return res.body?.string()
        }
    }

    @Serializable
    private data class AutomationsDto(val automations: List<AutomationInfo> = emptyList())

    @Serializable
    private data class ConfigGroupsDto(val groups: List<ConfigGroupInfo> = emptyList())

    @Serializable
    private data class SessionDto(
        val id: Int,
        val title: String? = null,
        @SerialName("started_at") val startedAt: String = "",
    )
}
