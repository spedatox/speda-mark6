package com.speda.heartbreaker

import android.content.Context
import com.speda.heartbreaker.data.HealthPoller
import com.speda.heartbreaker.data.UplinkStore
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * Manual dependency graph (plan §2 — Hilt is ceremony a solo, single-activity app
 * doesn't need). Constructed once in [HeartbreakerApp] and read from the Activity.
 *
 * The SSE streaming client (readTimeout = 0) arrives in M1; M0 only needs the
 * short-lived /health client, which uses ordinary timeouts.
 */
class AppGraph(context: Context) {

    val uplink: UplinkStore = UplinkStore(context.applicationContext)

    private val healthClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .callTimeout(15, TimeUnit.SECONDS)
        .build()

    val health: HealthPoller = HealthPoller(healthClient)
}
