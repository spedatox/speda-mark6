// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.sync

import android.content.Context
import android.os.Build
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.google.firebase.installations.FirebaseInstallations
import com.spedatox.atomixwear.AtomixWear
import com.spedatox.atomixwear.BuildConfig
import kotlinx.coroutines.tasks.await
import java.util.concurrent.TimeUnit

/**
 * Hands Igor this watch's Firebase Installation ID so the server can wake it.
 *
 * Until this succeeds the watch is unreachable and `live=true` queries fall back
 * to the fifteen-minute demand poll, so it retries with backoff rather than
 * giving up — an unregistered device is the difference between seconds and a
 * quarter of an hour.
 *
 * Registering by **Installation ID rather than registration token** matches
 * Ultron Wear and the current SDKs: `firebase-messaging` 25.1.0 deprecated
 * `getToken()`/`onNewToken()`, and the server SDKs deprecated
 * `Message(token=…)` in favour of `Message(fid=…)`. Tokens still work, but a new
 * integration built on them would need migrating inside the deprecation window.
 */
class RegisterDeviceWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as AtomixWear
        if (!app.igor.isConfigured) return Result.success()
        if (!BuildConfig.HAS_FIREBASE) {
            // No google-services.json in this build. Collection and upload work
            // fine; only the push wake is unavailable, which the demand poll
            // covers at lower urgency. Not a retryable condition.
            Log.i(TAG, "no Firebase config in this build — push wake unavailable")
            return Result.success()
        }

        val fid = try {
            FirebaseInstallations.getInstance().id.await()
        } catch (e: Exception) {
            Log.w(TAG, "could not read installation id: ${e.message}")
            return Result.retry()
        }

        return app.igor.registerDevice(deviceName(), fid, token = null).fold(
            onSuccess = {
                Log.i(TAG, "registered for push")
                Result.success()
            },
            onFailure = {
                Log.w(TAG, "registration failed: ${it.message}")
                Result.retry()
            },
        )
    }

    private fun deviceName(): String =
        listOf(Build.MANUFACTURER, Build.MODEL)
            .filter { it.isNotBlank() }
            .joinToString(" ")
            .take(96)

    companion object {
        private const val TAG = "RegisterDevice"
        const val WORK_NAME = "atomix-wear-register"

        /** KEEP: re-registering the same FID on every app start is pure noise.
         *  A genuine change (token rotation) enqueues explicitly. */
        fun enqueue(context: Context) {
            val request = OneTimeWorkRequestBuilder<RegisterDeviceWorker>()
                .setConstraints(
                    Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build(),
                )
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                WORK_NAME, ExistingWorkPolicy.KEEP, request,
            )
        }
    }
}
