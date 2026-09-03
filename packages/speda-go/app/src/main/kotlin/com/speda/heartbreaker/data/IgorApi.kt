// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.data

import com.speda.heartbreaker.domain.AircraftSpec
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.ChatMessage
import com.speda.heartbreaker.domain.MapPlace
import com.speda.heartbreaker.domain.RouteGeometry
import com.speda.heartbreaker.domain.Session
import com.speda.heartbreaker.domain.parseAircraftSpec
import com.speda.heartbreaker.domain.parsePlaceSet
import com.speda.heartbreaker.domain.parseRouteGeometry
import com.speda.heartbreaker.health.HealthIngestRequest
import com.speda.heartbreaker.health.HealthIngestResult
import com.speda.heartbreaker.health.HealthSampleDto
import com.speda.heartbreaker.health.HealthStatusDto
import com.speda.heartbreaker.i18n.AppStrings
import com.speda.heartbreaker.i18n.Tr
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
                        // Only when true: the backend defaults it to false, and an
                        // explicit "voice": false on every ordinary turn would be a
                        // key of noise on the hot path.
                        if (cc.voice) put("voice", true)
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

    /**
     * Real geometry for a route the agent referenced by id — the line, the live
     * congestion along it, and the turn-by-turn.
     *
     * None of it travels through the model. The polyline is ~500 delta-encoded
     * characters and a single mistyped one silently redraws the route into a
     * different valley; the congestion bands are indexed against that exact
     * polyline and are meaningless apart from it. The fence carries a
     * ten-character id instead and this fetches the truth. Null on any failure,
     * which the card renders as a missing line rather than a wrong one.
     */
    suspend fun fetchRouteGeometry(config: AppConfig, routeId: String): RouteGeometry? =
        withContext(Dispatchers.IO) {
            runCatching {
                getString(config, "/navigation/route/$routeId")?.let(::parseRouteGeometry)
            }.getOrNull()
        }

    /**
     * The full result set for a place search the agent referenced by id:
     * name, address, rating, open state, hours, phone, website, coordinates.
     *
     * Same rule as routes, for the same reason — a directory copied by hand
     * loses a digit off a phone number and rounds a rating, and none of it is
     * worth spending model tokens on twice. Null on failure, which leaves the
     * card with its routes and no place list rather than pins with invented
     * names.
     */
    suspend fun fetchPlaceSet(config: AppConfig, placesId: String): List<MapPlace>? =
        withContext(Dispatchers.IO) {
            runCatching {
                getString(config, "/navigation/places/$placesId")?.let(::parsePlaceSet)
            }.getOrNull()
        }

    /**
     * Live position + ADS-B status for a tail number, polled directly — unlike
     * routes/places there is no id-store behind this (see
     * app/services/aircraft.py): a live position changes every few seconds and
     * a tail number is short enough to carry safely without one. Null on any
     * failure, including "no current signal" (404) — the card keeps its last
     * known position rather than blanking.
     */
    suspend fun fetchAircraftTrack(config: AppConfig, tail: String): AircraftSpec? =
        withContext(Dispatchers.IO) {
            runCatching {
                getString(config, "/aircraft/track/$tail")?.let(::parseAircraftSpec)
            }.getOrNull()
        }

    /* ── Persistent reminders ──────────────────────────────────────────────
     * Standing reminders configured in Settings ▸ Reminders. Igor asks them on
     * a schedule and keeps asking until answered; these are the definitions,
     * not the runs.
     */

    suspend fun getReminders(config: AppConfig): List<ReminderDefinition> =
        withContext(Dispatchers.IO) {
            runCatching {
                getString(config, "/reminders/definitions")?.let { body ->
                    json.decodeFromString<ReminderDefinitionsResponse>(body).definitions
                }
            }.getOrNull() ?: emptyList()
        }

    suspend fun saveReminder(config: AppConfig, def: ReminderDefinition): Boolean =
        withContext(Dispatchers.IO) {
            runCatching {
                val body = json.encodeToString(
                    ReminderDefinitionBody.serializer(),
                    ReminderDefinitionBody.from(def),
                )
                val request = Request.Builder()
                    .url("${config.apiBase}/reminders/definitions/${def.id}")
                    .header("X-API-Key", config.apiKey)
                    .put(body.toRequestBody(jsonMedia))
                    .build()
                restClient.newCall(request).execute().use { it.isSuccessful }
            }.getOrDefault(false)
        }

    suspend fun deleteReminder(config: AppConfig, id: String): Boolean =
        withContext(Dispatchers.IO) {
            runCatching {
                val request = Request.Builder()
                    .url("${config.apiBase}/reminders/definitions/$id")
                    .header("X-API-Key", config.apiKey)
                    .delete()
                    .build()
                restClient.newCall(request).execute().use { it.isSuccessful }
            }.getOrDefault(false)
        }

    suspend fun getReminderHistory(config: AppConfig, limit: Int = 20): List<ReminderCycleInfo> =
        withContext(Dispatchers.IO) {
            runCatching {
                getString(config, "/reminders/history?limit=$limit")?.let { body ->
                    json.decodeFromString<ReminderHistoryResponse>(body).history
                }
            }.getOrNull() ?: emptyList()
        }

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

    suspend fun saveConfig(config: AppConfig, values: Map<String, JsonElement>, t: AppStrings = Tr): ConfigSaveResult = withContext(Dispatchers.IO) {
        runCatching {
            val body = buildJsonObject { put("values", JsonObject(values)) }
            putJson(config, "/config", body)?.let { json.decodeFromString<ConfigSaveResult>(it) }
        }.getOrNull() ?: ConfigSaveResult(rejected = listOf(t.settingsConfig.saveFailedBackend))
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

    /** Agents that can own an automation, for the builder's picker. */
    suspend fun getAutomationAgents(config: AppConfig): List<AutomationAgent> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/automations/agents")?.let {
                json.parseToJsonElement(it).jsonObject["agents"]
                    ?.let { agents -> json.decodeFromJsonElement<List<AutomationAgent>>(agents) }
            }
        }.getOrNull() ?: emptyList()
    }

    /** Create an automation. The backend validates; a refusal names the field
     *  and the fix, which is the only feedback the form has to give. */
    suspend fun createAutomation(config: AppConfig, draft: AutomationDraft): AutomationSaveResult =
        withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url("${config.apiBase}/automations")
                .header("X-API-Key", config.apiKey)
                .post(json.encodeToString(AutomationDraft.serializer(), draft).toRequestBody(jsonMedia))
                .build()
            runAutomationRequest(request)
        }

    /** Edit in place. The n8n workflow is updated, never recreated, so its
     *  "already fired today" memory and execution history survive the edit. */
    suspend fun updateAutomation(config: AppConfig, id: Int, draft: AutomationDraft): AutomationSaveResult =
        withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url("${config.apiBase}/automations/$id")
                .header("X-API-Key", config.apiKey)
                .put(json.encodeToString(AutomationDraft.serializer(), draft).toRequestBody(jsonMedia))
                .build()
            runAutomationRequest(request)
        }

    private fun runAutomationRequest(request: Request): AutomationSaveResult =
        runCatching {
            restClient.newCall(request).execute().use { res ->
                val text = res.body?.string().orEmpty()
                if (!res.isSuccessful) {
                    val msg = runCatching {
                        json.parseToJsonElement(text).jsonObject["error"]?.jsonPrimitive?.contentOrNull
                    }.getOrNull() ?: "HTTP ${res.code}"
                    return@use AutomationSaveResult.Error(msg)
                }
                AutomationSaveResult.Ok(json.decodeFromString<AutomationInfo>(text))
            }
        }.getOrElse { AutomationSaveResult.Error(it.message ?: "Could not reach the server.") }

    /**
     * Fire an automation's stored intent right now — the exact turn n8n would
     * start when its schedule comes due, not a mock. Never touches n8n's own
     * "already fired today" latch, so it cannot cause or be mistaken for a
     * duplicate real firing.
     */
    suspend fun testAutomation(config: AppConfig, id: Int): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            postEmpty(config, "/automations/$id/test")?.let {
                json.parseToJsonElement(it).jsonObject["error"] == null
            } ?: false
        }.getOrDefault(false)
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

    // ── The standing protocols ──────────────────────────────────────────────
    //
    // Read-only status for the four host protocols, plus the whole of Skyfall.
    // Every read stamps `reachable = true` on success and returns the fallback
    // with `reachable = false` otherwise: an unreachable server is NOT evidence
    // that a protocol is disabled, and the pane needs to be able to tell those
    // apart or it will send the owner to change a setting that was never wrong.
    //
    // House Party is deliberately absent. It stages the whole roster in a war
    // room the phone does not build, so the backend refuses to engage it from
    // here (app/core/surface.py) — the pane shows it as desktop-only rather than
    // offering a control that would be refused.

    suspend fun fetchLockdown(config: AppConfig): LockdownState = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/agents/lockdown")
                ?.let { json.decodeFromString<LockdownState>(it).copy(reachable = true) }
        }.getOrNull() ?: LockdownState()
    }

    /** Stand containment down. Never takes a passphrase — the way out of a
     *  lockdown must always be available, including from the phone. */
    suspend fun standDownLockdown(config: AppConfig): String? = withContext(Dispatchers.IO) {
        runCatching {
            postJson(config, "/agents/lockdown", buildJsonObject { put("engaged", false) })
        }.getOrNull()
    }

    /** Engage containment with the owner's authorization passphrase. */
    suspend fun engageLockdown(config: AppConfig, passphrase: String): Boolean =
        withContext(Dispatchers.IO) {
            runCatching {
                postJson(config, "/agents/lockdown", buildJsonObject {
                    put("engaged", true); put("passphrase", passphrase)
                }) != null
            }.getOrDefault(false)
        }

    suspend fun fetchLifeboat(config: AppConfig): LifeboatState = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/host/lifeboat")
                ?.let { json.decodeFromString<LifeboatState>(it).copy(reachable = true) }
        }.getOrNull() ?: LifeboatState()
    }

    suspend fun fetchOctavius(config: AppConfig): OctaviusState = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/admin/octavius")
                ?.let { json.decodeFromString<OctaviusState>(it).copy(reachable = true) }
        }.getOrNull() ?: OctaviusState()
    }

    /** Take a backup now. Minutes on a large database, so the caller keeps its
     *  button disabled throughout rather than assuming speed. */
    suspend fun runOctaviusBackup(config: AppConfig): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            postEmpty(config, "/admin/octavius/backup")?.let {
                json.parseToJsonElement(it).jsonObject["ok"]?.jsonPrimitive?.booleanOrNull
            } ?: false
        }.getOrDefault(false)
    }

    suspend fun fetchDoormat(config: AppConfig): DoormatState = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/host/doormat")
                ?.let { json.decodeFromString<DoormatState>(it).copy(reachable = true) }
        }.getOrNull() ?: DoormatState()
    }

    // ── Skyfall ─────────────────────────────────────────────────────────────

    suspend fun fetchSkyfallProjects(config: AppConfig): List<SkyfallProject> =
        withContext(Dispatchers.IO) {
            runCatching {
                getString(config, "/protocols/skyfall/projects")?.let {
                    json.decodeFromString<List<SkyfallProject>>(it)
                }
            }.getOrNull() ?: emptyList()
        }

    /** Create or update a project. Returns the server's own words on a refusal —
     *  it owns the rules, and a second copy of them here would drift. */
    suspend fun saveSkyfallProject(
        config: AppConfig,
        project: SkyfallProject,
    ): String? = withContext(Dispatchers.IO) {
        val body = buildJsonObject {
            put("id", project.id)
            put("name", project.name)
            put("description", project.description)
            put("url", project.url)
            put("method", project.method)
            put("body", project.body)
            put("countdown_seconds", project.countdownSeconds)
            put("headers", buildJsonObject { project.headers.forEach { (k, v) -> put(k, v) } })
        }
        val request = Request.Builder()
            .url("${config.apiBase}/protocols/skyfall/projects")
            .header("X-API-Key", config.apiKey)
            .put(body.toString().toRequestBody(jsonMedia))
            .build()
        runCatching {
            restClient.newCall(request).execute().use { res ->
                val text = res.body?.string().orEmpty()
                if (res.isSuccessful) null
                else json.parseToJsonElement(text).jsonObject["detail"]
                    ?.jsonPrimitive?.contentOrNull ?: "HTTP ${res.code}"
            }
        }.getOrElse { "Could not reach the server." }
    }

    suspend fun deleteSkyfallProject(config: AppConfig, id: String): Boolean =
        withContext(Dispatchers.IO) {
            runCatching { deleteRequest(config, "/protocols/skyfall/projects/$id") != null }
                .getOrDefault(false)
        }

    /** The countdown payload for a project picked from the list. Sends nothing —
     *  arming IS opening the clock, and the clock belongs to the screen. */
    suspend fun armSkyfall(config: AppConfig, id: String): SkyfallArm? =
        withContext(Dispatchers.IO) {
            runCatching {
                postEmpty(config, "/protocols/skyfall/arm/$id")?.let {
                    json.decodeFromString<SkyfallArm>(it)
                }
            }.getOrNull()
        }

    /** The clock reached zero. The countdown screen is the only caller. */
    suspend fun fireSkyfall(config: AppConfig, id: String): SkyfallResult =
        withContext(Dispatchers.IO) {
            runCatching {
                postJson(config, "/protocols/skyfall/fire", buildJsonObject { put("project_id", id) })
                    ?.let { json.decodeFromString<SkyfallResult>(it) }
                // A null body is a non-2xx: a deleted or unusable project, which
                // means nothing was sent. Say that rather than implying a launch.
                    ?: SkyfallResult(fired = false, error = "The project could not be fired.")
            }.getOrElse {
                // The request may or may not have left the phone. `fired = true`
                // is the honest answer — the screen must not report that nothing
                // happened when it cannot know that.
                SkyfallResult(
                    fired = true, ok = false,
                    error = "Lost contact mid-launch — whether the request went out is unknown.",
                )
            }
        }

    /** Record that the owner stopped the clock. Best-effort: the abort already
     *  happened by NOT firing, and this only writes it down. */
    suspend fun abortSkyfall(config: AppConfig, id: String, remaining: Double) {
        withContext(Dispatchers.IO) {
            runCatching {
                postJson(config, "/protocols/skyfall/abort", buildJsonObject {
                    put("project_id", id); put("remaining_seconds", remaining)
                })
            }
        }
    }

    /*
     * getHouseParty / setHouseParty used to live here. The House Party Protocol
     * is a desktop surface now — the backend refuses to ENGAGE it from a
     * non-desktop client (app/core/surface.py) — and nothing in this app calls
     * the endpoint any more. The owner can still stand the protocol down from
     * the phone by telling Speda, which goes through the `house_party` tool
     * rather than this client.
     */

    /* ── Pending owner approvals (GET /agents/asks, POST /agents/asks/{id}) ──
     * Irreversible operations an external peer's safety gate has stopped.
     *
     * fetchPendingAsks is the GUARANTEED path to an open ask. A chat job's ask
     * also arrives inline on its own stream as a `permission_request` SSE frame,
     * but a peer raises that only when the ask carries a chat_id — the Forge's
     * peer oracle attaches none, so a dispatched or background job's ask never
     * reaches the stream at all, and an app that was closed when the ask was
     * raised never saw one either. This endpoint is agent-agnostic: every open
     * ask carries its own agent_id, so one poll covers the whole external roster.
     */

    suspend fun fetchPendingAsks(config: AppConfig): List<PendingAsk> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/agents/asks")?.let { json.decodeFromString<List<PendingAsk>>(it) }
        }.getOrNull() ?: emptyList()
    }

    /** Send the owner's decision down to the peer. A 404 means the ask is
     *  gone — expired, already answered, or its agent disconnected — and is NOT
     *  a retry condition: the peer runs its own countdown and has already denied
     *  locally, so the operation did not happen either way. */
    suspend fun answerAsk(
        config: AppConfig,
        askId: String,
        approved: Boolean,
        remember: Boolean = false,
        note: String = "",
    ): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            val body = buildJsonObject { put("approved", approved); put("remember", remember); put("note", note) }
            postJson(config, "/agents/asks/${java.net.URLEncoder.encode(askId, "UTF-8")}", body) != null
        }.getOrDefault(false)
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

    /** A SECOND pin per agent, for turns that arrive over Telegram (POST
     *  /agents/telegram-models). Separate from the app pin on purpose: a phone
     *  reply is short and usually cheap, and pinning the interactive core for it
     *  would spend the interactive rate on every "ok". Read side is the same
     *  [fetchAgentModels] list — [AgentModelInfo.telegramOverride] carries it. */
    suspend fun pinTelegramModel(config: AppConfig, agentId: String, model: String?): List<AgentModelInfo> =
        withContext(Dispatchers.IO) {
            runCatching {
                val body = buildJsonObject { put("agent_id", agentId); put("model", model) }
                postJson(config, "/agents/telegram-models", body)?.let {
                    json.decodeFromString<List<AgentModelInfo>>(it)
                }
            }.getOrNull() ?: emptyList()
        }

    // ── Legion worker model routing (GET/POST /agents/legion-models) ─────────────

    suspend fun fetchLegionModels(config: AppConfig): List<LegionModelInfo> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/agents/legion-models")?.let { json.decodeFromString<List<LegionModelInfo>>(it) }
        }.getOrNull() ?: emptyList()
    }

    /** Pin a legionnaire to a model ref; null clears it (back to effort policy). */
    suspend fun pinLegionModel(config: AppConfig, workerId: String, model: String?): List<LegionModelInfo> = withContext(Dispatchers.IO) {
        runCatching {
            val body = buildJsonObject { put("worker_id", workerId); put("model", model) }
            postJson(config, "/agents/legion-models", body)?.let { json.decodeFromString<List<LegionModelInfo>>(it) }
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

    /** Whether the import failed used to be inferred by the caller matching an
     *  English string prefix on the returned message — which breaks the moment
     *  that message is localized. [ok] carries the outcome explicitly instead. */
    data class ImportOutcome(val ok: Boolean, val message: String)

    suspend fun importChats(config: AppConfig, fileName: String, bytes: ByteArray, t: AppStrings = Tr): ImportOutcome = withContext(Dispatchers.IO) {
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
                if (!res.isSuccessful) return@use ImportOutcome(false, t.settingsData.importFailedHttp(res.code))
                val message = json.parseToJsonElement(body).jsonObject["message"]?.jsonPrimitive?.contentOrNull
                    ?: t.settingsData.importStartedBackground
                ImportOutcome(true, message)
            }
        }.getOrElse { ImportOutcome(false, t.settingsData.importFailedError(it.message.orEmpty())) }
    }

    suspend fun indexHistory(config: AppConfig, t: AppStrings = Tr): String = withContext(Dispatchers.IO) {
        runCatching {
            postEmpty(config, "/admin/index-history")?.let {
                json.parseToJsonElement(it).jsonObject["message"]?.jsonPrimitive?.contentOrNull
            } ?: t.settingsData.indexingStartedBackground
        }.getOrElse { t.settingsData.indexFailedError(it.message.orEmpty()) }
    }

    // ── Atomix health sync (docs/ATOMIX_HEALTH_SYNC.md §3.1) ───────────────────

    /** POST a batch of biometrics. Returns null on any failure so the caller
     *  leaves its changes token un-advanced and retries the same window. */
    suspend fun ingestHealth(
        config: AppConfig,
        device: String,
        samples: List<HealthSampleDto>,
    ): HealthIngestResult? = withContext(Dispatchers.IO) {
        runCatching {
            val body = json.encodeToString(
                HealthIngestRequest.serializer(),
                HealthIngestRequest(device = device, samples = samples),
            )
            val request = Request.Builder()
                .url("${config.apiBase}/health/ingest")
                .header("X-API-Key", config.apiKey)
                .post(body.toRequestBody(jsonMedia))
                .build()
            restClient.newCall(request).execute().use { res ->
                if (!res.isSuccessful) return@runCatching null
                res.body?.string()?.let { json.decodeFromString<HealthIngestResult>(it) }
            }
        }.getOrNull()
    }

    suspend fun healthStatus(config: AppConfig): HealthStatusDto? = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/health/status")?.let { json.decodeFromString<HealthStatusDto>(it) }
        }.getOrNull()
    }

    suspend fun wipeHealth(config: AppConfig): Boolean = withContext(Dispatchers.IO) {
        runCatching { deleteRequest(config, "/health/data") != null }.getOrDefault(false)
    }

    /**
     * Hand Igor this installation's Firebase Installation ID so it can push here.
     *
     * FID, not a registration token: firebase-messaging 25.1.x deprecated the
     * whole token API and the Admin SDKs moved to `Message(fid=…)` to match.
     * Ultron Wear registers the same way against the same endpoint — the
     * `platform` field is what keeps a health-sync wake off the watch.
     */
    suspend fun registerDevice(
        config: AppConfig,
        deviceId: String,
        platform: String,
        fid: String,
    ): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            val body = buildJsonObject {
                put("device", deviceId)
                put("platform", platform)
                put("fid", fid)
            }
            postJson(config, "/devices/register", body) != null
        }.getOrDefault(false)
    }

    /**
     * Is Igor waiting on a health sync right now?
     *
     * Atomix raises this when a turn needs biometrics that describe the present
     * — a morning briefing will refuse to report at all rather than pass off a
     * four-day-old resting heart rate as today's. There is no push channel to
     * this app, so the demand is a note left on the server and this is the app
     * going to look. Cheap by design: one small GET, no body worth parsing.
     *
     * Returns false on any failure — a server we cannot reach is not a server
     * asking us for anything, and a demand we miss costs one stale briefing,
     * while a retry storm costs battery every fifteen minutes forever.
     */
    suspend fun healthSyncDemanded(config: AppConfig): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            val body = getString(config, "/health/sync-demand") ?: return@runCatching false
            json.parseToJsonElement(body).jsonObject["outstanding"]
                ?.jsonPrimitive?.booleanOrNull ?: false
        }.getOrDefault(false)
    }

    /* ── Custom MCP servers (GET/POST/DELETE /connections/mcp) ──────────────
     * A Tier-2 capability the owner wires up without a code change: a server is
     * a command or a URL plus credentials, so adding one needs no build here.
     */

    suspend fun getCustomMcpServers(config: AppConfig): CustomMcpResult = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/connections/mcp")?.let { json.decodeFromString<CustomMcpResult>(it) }
        }.getOrNull() ?: CustomMcpResult()
    }

    /** Register or update one server. A masked credential value sent back
     *  UNCHANGED means "keep the stored secret" — that is what lets the owner
     *  fix a note or a header without retyping a token they can no longer read. */
    suspend fun saveCustomMcpServer(
        config: AppConfig,
        name: String,
        transport: String,
        command: String,
        url: String,
        env: Map<String, String>,
        headers: Map<String, String>,
        enabled: Boolean,
        note: String,
    ): McpSaveResult = withContext(Dispatchers.IO) {
        runCatching {
            val body = buildJsonObject {
                put("name", name)
                put("transport", transport)
                put("command", command)
                put("url", url)
                put("env", buildJsonObject { env.forEach { (k, v) -> put(k, v) } })
                put("headers", buildJsonObject { headers.forEach { (k, v) -> put(k, v) } })
                put("enabled", enabled)
                put("note", note)
            }
            postJson(config, "/connections/mcp", body)?.let { json.decodeFromString<McpSaveResult>(it) }
        }.getOrNull() ?: McpSaveResult(error = "request failed")
    }

    suspend fun deleteCustomMcpServer(config: AppConfig, name: String): Boolean = withContext(Dispatchers.IO) {
        runCatching { deleteRequest(config, "/connections/mcp/${java.net.URLEncoder.encode(name, "UTF-8")}") != null }
            .getOrDefault(false)
    }

    /* ── Web portals (GET/POST/DELETE /connections/portals) ─────────────────
     * A portal is an ACCOUNT, not a scraping target. The browser container
     * keeps the cookies, this side keeps the credentials, and the two never
     * swap jobs.
     */

    suspend fun getPortals(config: AppConfig): PortalsResult = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/connections/portals")?.let { json.decodeFromString<PortalsResult>(it) }
        }.getOrNull() ?: PortalsResult(browser = BrowserStatus(status = "down", reason = "unreachable"))
    }

    /** Store a portal, optionally testing the login while doing it. [password]
     *  travels app to backend to container to page and stops there — it is never
     *  put in a tool argument and nothing here may ever hand it to a model
     *  (CLAUDE.md, Security). Send the MASKED value back untouched to keep the
     *  stored one. */
    suspend fun savePortal(
        config: AppConfig,
        name: String,
        label: String,
        loginUrl: String,
        homeUrl: String,
        username: String,
        password: String,
        note: String,
        enabled: Boolean,
        allowedAgents: List<String>,
        test: Boolean,
    ): PortalActionResult = withContext(Dispatchers.IO) {
        runCatching {
            val body = buildJsonObject {
                put("name", name)
                put("label", label)
                put("login_url", loginUrl)
                put("home_url", homeUrl)
                put("username", username)
                put("password", password)
                put("note", note)
                put("enabled", enabled)
                put("allowed_agents", buildJsonArray { allowedAgents.forEach { add(it) } })
                put("test", test)
            }
            postJson(config, "/connections/portals", body)?.let { json.decodeFromString<PortalActionResult>(it) }
        }.getOrNull() ?: PortalActionResult(error = "request failed")
    }

    /** Sign in now, by portal NAME. Minutes are possible — the container renders
     *  a real page — so the caller holds its button disabled throughout. */
    suspend fun portalLogin(config: AppConfig, name: String): PortalActionResult = withContext(Dispatchers.IO) {
        runCatching {
            postEmpty(config, "/connections/portals/${java.net.URLEncoder.encode(name, "UTF-8")}/login")?.let {
                json.decodeFromString<PortalActionResult>(it)
            }
        }.getOrNull() ?: PortalActionResult(error = "request failed")
    }

    /** Drop the container's cookies for this portal, keeping the account: the
     *  credentials stay, only the session goes. */
    suspend fun portalForget(config: AppConfig, name: String): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            postEmpty(config, "/connections/portals/${java.net.URLEncoder.encode(name, "UTF-8")}/forget") != null
        }.getOrDefault(false)
    }

    suspend fun deletePortal(config: AppConfig, name: String): Boolean = withContext(Dispatchers.IO) {
        runCatching { deleteRequest(config, "/connections/portals/${java.net.URLEncoder.encode(name, "UTF-8")}") != null }
            .getOrDefault(false)
    }

    /* ── Memory record health (GET /admin/memory/status, /memory/folders) ──── */

    /** Where the observation record stands. No model call, so it is cheap
     *  enough to read whenever the screen showing it is open. */
    suspend fun memoryStatus(config: AppConfig): MemoryStatus? = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/admin/memory/status")?.let { json.decodeFromString<MemoryStatus>(it) }
        }.getOrNull()
    }

    /** Folders the store DECLARES, including ones holding no file yet — a folder
     *  with no files does not exist in the files table at all, so without this
     *  the knowledge bank cannot show where a thing WILL go before something has
     *  gone there. */
    suspend fun fetchMemoryFolders(config: AppConfig): List<MemoryFolderInfo> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/memory/folders")?.let { json.decodeFromString<List<MemoryFolderInfo>>(it) }
        }.getOrNull() ?: emptyList()
    }

    /**
     * Fetch one board picture through Igor (GET /media/proxy).
     *
     * The URL is a third party's and the fetch deliberately does NOT happen on
     * the phone: a client that loaded a photo straight from its origin would
     * tell that origin the owner's IP and the moment he looked, which on a board
     * about a person is the one thing it must not do. The server is already
     * making requests of its own, so it makes this one.
     *
     * Null on anything that goes wrong — a dead link, a host that refuses, a
     * file that is not an image. The window then renders without its picture,
     * which is the intended degradation.
     */
    suspend fun fetchBoardImage(config: AppConfig, url: String): ByteArray? = withContext(Dispatchers.IO) {
        runCatching {
            val request = Request.Builder()
                .url("${config.apiBase}/media/proxy?url=${encodePath(url)}")
                .header("X-API-Key", config.apiKey)
                .get()
                .build()
            restClient.newCall(request).execute().use { res ->
                if (!res.isSuccessful) null else res.body?.bytes()
            }
        }.getOrNull()
    }

    /**
     * Synthesize ONE utterance (POST /voice/speak) and return the MP3 bytes.
     *
     * A sentence at a time, not a whole reply: playback of sentence N overlaps
     * synthesis of N+1, which is what keeps a spoken answer starting promptly
     * instead of after the last word has been generated.
     *
     * Null on failure, deliberately quiet. The backend answers 503 when voice is
     * unconfigured or the engine refused, and one silent sentence is a far better
     * outcome than a dead turn — the reply is on screen either way.
     */
    suspend fun speak(
        config: AppConfig,
        text: String,
        agentId: String?,
        locale: String? = null,
    ): ByteArray? = withContext(Dispatchers.IO) {
        runCatching {
            val body = buildJsonObject {
                put("text", text)
                put("agent_id", agentId)
                if (locale != null) put("locale", locale)
            }
            val request = Request.Builder()
                .url("${config.apiBase}/voice/speak")
                .header("X-API-Key", config.apiKey)
                .post(body.toString().toRequestBody(jsonMedia))
                .build()
            restClient.newCall(request).execute().use { res ->
                if (!res.isSuccessful) null else res.body?.bytes()
            }
        }.getOrNull()
    }

    /* ── Voices (GET /voice/agents, /voice/voices, PUT /voice/agents/{id}) ───
     * Per-agent voice pin + ElevenLabs tuning. Two things clear independently:
     * the PIN (falls back to the profile's own default) and the TUNING (falls
     * back to that voice's own ElevenLabs dashboard settings). [clearVoiceAgent]
     * clears both at once for a clean slate.
     */

    suspend fun fetchVoiceAgents(config: AppConfig): List<VoiceAgentInfo> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/voice/agents")?.let {
                json.parseToJsonElement(it).jsonObject["agents"]
                    ?.let { agents -> json.decodeFromJsonElement<List<VoiceAgentInfo>>(agents) }
            }
        }.getOrNull() ?: emptyList()
    }

    /** Spans every configured engine — Azure, OpenAI, and the owner's own
     *  ElevenLabs voice library. */
    suspend fun fetchVoiceOptions(config: AppConfig): List<VoiceOption> = withContext(Dispatchers.IO) {
        runCatching {
            getString(config, "/voice/voices")?.let {
                json.parseToJsonElement(it).jsonObject["voices"]
                    ?.let { voices -> json.decodeFromJsonElement<List<VoiceOption>>(voices) }
            }
        }.getOrNull() ?: emptyList()
    }

    /** Save a pin and/or tuning. Pass null for [voiceId] to clear the pin back
     *  to the profile default; only the tuning keys that actually changed need
     *  sending — omitted ones are left as they were. */
    suspend fun saveVoiceAgent(
        config: AppConfig,
        agentId: String,
        voiceId: String?,
        stability: Float,
        similarityBoost: Float,
        style: Float,
        speed: Float,
        useSpeakerBoost: Boolean,
    ): List<VoiceAgentInfo> = withContext(Dispatchers.IO) {
        runCatching {
            val body = buildJsonObject {
                put("voice_id", voiceId)
                put("stability", stability)
                put("similarity_boost", similarityBoost)
                put("style", style)
                put("speed", speed)
                put("use_speaker_boost", useSpeakerBoost)
            }
            putJson(config, "/voice/agents/${encodePath(agentId)}", body)?.let {
                json.parseToJsonElement(it).jsonObject["agents"]
                    ?.let { agents -> json.decodeFromJsonElement<List<VoiceAgentInfo>>(agents) }
            }
        }.getOrNull() ?: emptyList()
    }

    /** Clear every override for this agent in one call — back to the profile
     *  default, an empty patch same as the desktop sends. */
    suspend fun clearVoiceAgent(config: AppConfig, agentId: String): List<VoiceAgentInfo> = withContext(Dispatchers.IO) {
        runCatching {
            putJson(config, "/voice/agents/${encodePath(agentId)}", buildJsonObject { })?.let {
                json.parseToJsonElement(it).jsonObject["agents"]
                    ?.let { agents -> json.decodeFromJsonElement<List<VoiceAgentInfo>>(agents) }
            }
        }.getOrNull() ?: emptyList()
    }

    // ── helpers ────────────────────────────────────────────────────────────────

    private fun encodePath(s: String): String = java.net.URLEncoder.encode(s, "UTF-8")

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
