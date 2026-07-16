package com.speda.heartbreaker.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import kotlin.system.measureTimeMillis

/** Live connection telemetry, mirror of lib/useHealth.ts `Health`. */
data class Health(
    val online: Boolean,
    val latencyMs: Long?,
    val tools: Int?,
) {
    companion object {
        val Offline = Health(online = false, latencyMs = null, tools = null)
    }
}

/**
 * Polls GET /health for reachability, round-trip latency and the live registered
 * tool count — a literal port of useHealth.ts. The default 8 s cadence matches
 * the web; the systems board uses 4 s. Emissions are collected under
 * repeatOnLifecycle(STARTED) at the call site, so polling pauses in the
 * background and resumes with an immediate tick (plan §4.2).
 */
class HealthPoller(private val client: OkHttpClient) {

    private val json = Json { ignoreUnknownKeys = true }

    fun poll(apiBase: String, apiKey: String, intervalMs: Long = 8_000L): Flow<Health> = flow {
        while (true) {
            emit(ping(apiBase, apiKey))
            delay(intervalMs)
        }
    }.flowOn(Dispatchers.IO)

    private fun ping(apiBase: String, apiKey: String): Health {
        return try {
            val request = Request.Builder()
                .url("$apiBase/health")
                .apply { if (apiKey.isNotEmpty()) header("X-API-Key", apiKey) }
                .get()
                .build()

            var tools: Int? = null
            var ok = false
            val dt = measureTimeMillis {
                client.newCall(request).execute().use { res ->
                    ok = res.isSuccessful
                    if (ok) {
                        val body = res.body?.string().orEmpty()
                        tools = runCatching {
                            json.parseToJsonElement(body).jsonObject["tools_registered"]?.jsonPrimitive?.intOrNull
                        }.getOrNull()
                    }
                }
            }
            if (ok) Health(online = true, latencyMs = dt, tools = tools) else Health.Offline
        } catch (_: Exception) {
            Health.Offline
        }
    }
}
