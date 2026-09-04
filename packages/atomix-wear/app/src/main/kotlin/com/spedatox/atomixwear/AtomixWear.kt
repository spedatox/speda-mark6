// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear

import android.app.Application
import android.util.Log
import com.spedatox.atomixwear.data.IgorClient
import com.spedatox.atomixwear.data.SampleQueue
import com.spedatox.atomixwear.health.BiometricSource
import com.spedatox.atomixwear.sync.RegisterDeviceWorker
import com.spedatox.atomixwear.sync.SyncScheduler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.io.File

/**
 * The object graph.
 *
 * Dependencies are constructed by hand, as Ultron Wear does: the graph is four
 * singletons with no scoping requirements, and a DI container would add a
 * processor and a startup cost to solve a problem this app does not have.
 *
 * Everything here is reachable from the activity, the workers and the
 * system-invoked services, which is the reason it lives on [Application] — a
 * passive-data callback can be delivered to a process with no activity in it at
 * all, and must still find a working queue.
 */
class AtomixWear : Application() {

    /**
     * Application-lifetime scope for work that must outlive the component that
     * started it — chiefly the queue write in the passive-data callback, which
     * has to complete after the service method returns.
     *
     * [SupervisorJob] so one failed child never cancels the others: a failed
     * upload must not take collection down with it.
     */
    val appScope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    val igor: IgorClient by lazy { IgorClient() }

    val queue: SampleQueue by lazy {
        SampleQueue(File(filesDir, SampleQueue.FILE_NAME))
    }

    val biometrics: BiometricSource by lazy { BiometricSource(this) }

    override fun onCreate() {
        super.onCreate()

        if (!igor.isConfigured) {
            // Worth saying once, loudly. A build with no IGOR_BASE_URL collects
            // and queues but can never upload, and the symptom — data on the
            // watch, nothing on the server — is otherwise indistinguishable
            // from a broken endpoint.
            Log.w(TAG, "IGOR_BASE_URL/IGOR_API_KEY absent — collecting locally, uploads disabled")
        }

        SyncScheduler.ensureScheduled(this)
        RegisterDeviceWorker.enqueue(this)

        // Re-assert passive collection on every start. Registration is
        // idempotent (it replaces the existing config), so this covers the
        // cases a BootReceiver cannot: a fresh install, a permission granted
        // while the app was closed, an app upgrade.
        appScope.launch {
            val types = biometrics.register()
            Log.i(TAG, "collecting ${types.size} passive data type(s)")
        }
    }

    private companion object { const val TAG = "AtomixWear" }
}
