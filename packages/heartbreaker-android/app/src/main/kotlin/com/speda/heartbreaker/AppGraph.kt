package com.speda.heartbreaker

import android.content.Context
import com.speda.heartbreaker.data.Downloader
import com.speda.heartbreaker.data.HealthPoller
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.MessageCache
import com.speda.heartbreaker.data.PlatformContextProvider
import com.speda.heartbreaker.data.SettingsStore
import com.speda.heartbreaker.data.UplinkStore
import com.speda.heartbreaker.health.HealthSyncManager
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * Manual dependency graph (plan §2 — Hilt is ceremony a solo, single-activity app
 * doesn't need). Constructed once in [HeartbreakerApp] and read from the Activity.
 */
class AppGraph(context: Context) {

    private val appContext = context.applicationContext

    val uplink: UplinkStore = UplinkStore(appContext)
    val settings: SettingsStore = SettingsStore(appContext)
    val platform: PlatformContextProvider = PlatformContextProvider(appContext)

    // Short-lived REST + /health client (ordinary timeouts).
    private val restClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .callTimeout(15, TimeUnit.SECONDS)
        .build()

    // Streaming client — reads idle for the length of a turn; the watchdog owns
    // liveness, so read/call timeouts are disabled (plan §4.1).
    private val streamClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .callTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    val health: HealthPoller = HealthPoller(restClient)
    val api: IgorApi = IgorApi(streamClient = streamClient, restClient = restClient)
    val messageCache: MessageCache = MessageCache(appContext.cacheDir)
    val downloader: Downloader = Downloader(appContext, restClient)

    /** Atomix health sync — Health Connect → Igor (docs/ATOMIX_HEALTH_SYNC.md). */
    val healthSync: HealthSyncManager = HealthSyncManager(appContext, settings, api)
}
