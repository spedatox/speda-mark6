// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.data

import android.util.Log
import com.spedatox.atomixwear.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * The Igor transport, ported from Ultron Wear.
 *
 * Deliberately `HttpURLConnection` rather than OkHttp, which is what Speda GO
 * uses. This app makes a handful of small REST calls with no streaming and no
 * interceptors; OkHttp would add roughly 800 KB of dex and a chunk of class
 * loading to the cold start of a watch app. The platform client is enough.
 *
 * Every call is `Dispatchers.IO` and returns a [Result] — the watch is offline
 * often and by design, so a failed call is an ordinary outcome the caller
 * handles, never an exception that reaches the UI.
 *
 * AUTH: Igor's Rule 12 requires `X-API-Key` on every endpoint. The key is
 * injected at build time from `local.properties`, so it is never committed. A
 * build with no key degrades to collect-and-queue rather than shipping a
 * placeholder that would 401 on every call — samples still accumulate locally
 * and upload once a configured build replaces it.
 */
class IgorClient(
    private val baseUrl: String = BuildConfig.IGOR_BASE_URL,
    private val apiKey: String = BuildConfig.IGOR_API_KEY,
) {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    val isConfigured: Boolean
        get() = baseUrl.isNotBlank() && apiKey.isNotBlank()

    /**
     * Push a batch of biometrics. The endpoint is idempotent on
     * `(metric, start_ts, origin)`, which is what makes the retry discipline in
     * [com.spedatox.atomixwear.sync.HealthSyncWorker] safe: a batch whose POST
     * failed is re-sent next cycle and collapses server-side instead of
     * duplicating.
     */
    suspend fun ingestHealth(
        device: String,
        samples: List<HealthSampleDto>,
    ): Result<HealthIngestResponse> {
        val payload = HealthIngestRequest(device = device, samples = samples)
        return request("POST", "/health/ingest", json.encodeToString(payload)) { raw ->
            json.decodeFromString<HealthIngestResponse>(raw)
        }
    }

    /**
     * Is Igor waiting on a sync right now?
     *
     * Belt-and-braces alongside the FCM wake (§3.3). Push is the fast path, but
     * a watch that was unreachable when the demand was raised — off-wrist,
     * flight mode, a rejected push — would otherwise never learn about it. This
     * is the same note-where-the-app-will-look mechanism Speda GO depends on
     * entirely; here it is only the fallback.
     */
    suspend fun syncDemand(): Result<SyncDemandResponse> =
        request("GET", "/health/sync-demand", body = null) { raw ->
            json.decodeFromString<SyncDemandResponse>(raw)
        }

    /** Hand Igor this watch's Firebase Installation ID so it can be woken. */
    suspend fun registerDevice(device: String, fid: String, token: String?): Result<Unit> {
        val payload = DeviceRegisterRequest(device = device, fid = fid, token = token)
        return request("POST", "/devices/register", json.encodeToString(payload)) { }
    }

    private suspend fun <T> request(
        method: String,
        path: String,
        body: String?,
        parse: (String) -> T,
    ): Result<T> = withContext(Dispatchers.IO) {
        if (!isConfigured) {
            return@withContext Result.failure(IllegalStateException("Igor endpoint not configured"))
        }
        var conn: HttpURLConnection? = null
        try {
            conn = (URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection).apply {
                requestMethod = method
                // Short timeouts on purpose: this runs opportunistically in the
                // background. Hanging for 30s on a dead network keeps a radio
                // and a wakelock alive for nothing — which on a watch is a
                // visible battery cost, not a rounding error.
                connectTimeout = 8_000
                readTimeout = 12_000
                setRequestProperty("X-API-Key", apiKey)
                setRequestProperty("Accept", "application/json")
                if (body != null) {
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json; charset=utf-8")
                }
            }
            body?.let { conn.outputStream.use { os -> os.write(it.toByteArray()) } }

            val code = conn.responseCode
            if (code !in 200..299) {
                val err = conn.errorStream?.bufferedReader()?.use(BufferedReader::readText).orEmpty()
                Log.w(TAG, "$method $path -> $code $err")
                return@withContext Result.failure(IgorHttpException(code, err))
            }
            val raw = conn.inputStream.bufferedReader().use(BufferedReader::readText)
            Result.success(parse(raw))
        } catch (e: Exception) {
            Log.w(TAG, "$method $path failed: ${e.message}")
            Result.failure(e)
        } finally {
            conn?.disconnect()
        }
    }

    private companion object { const val TAG = "IgorClient" }
}

class IgorHttpException(val status: Int, val body: String) :
    Exception("Igor returned $status: ${body.take(200)}")
