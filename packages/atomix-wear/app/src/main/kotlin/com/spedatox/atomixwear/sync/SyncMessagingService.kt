// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.sync

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/**
 * The push wake — the mechanism that makes a `live=true` health query answerable
 * at all (docs/ATOMIX_WEAR.md §1.2).
 *
 * `/health/sync-demand` exists in Igor because, as `routers/health.py` puts it,
 * *"Speda GO carries no Firebase, so nothing can wake it from the server side"* —
 * the demand is a note left where the app will eventually look. This service is
 * what turns that note into a tap on the shoulder, and it is the single largest
 * behavioural difference between this client and the phone's.
 *
 * ## Data-only, always
 *
 * The server must send a message with NO `notification` block. A message
 * carrying one is rendered by the system tray while the app is backgrounded and
 * [onMessageReceived] is never invoked — which would mean no sync, no upload,
 * and a briefing that still reports stale data, with a useless tray entry as the
 * only evidence anything happened. Combined with `android.priority = high`, a
 * data-only message guarantees this handler runs.
 */
class SyncMessagingService : FirebaseMessagingService() {

    override fun onMessageReceived(message: RemoteMessage) {
        val reason = message.data["reason"].orEmpty()
        Log.i(TAG, "wake received${if (reason.isNotBlank()) " ($reason)" else ""}")
        // Expedited upload. Everything already collected is on disk; this is
        // purely a transport trigger, so there is nothing to collect first.
        SyncScheduler.syncNow(applicationContext)
    }

    /**
     * Registration is by Firebase Installation ID, not by this token —
     * `firebase-messaging` 25.1.0 deprecated `getToken`/`onNewToken` in favour
     * of the FID, and the server SDKs deprecated `Message(token=…)` for
     * `Message(fid=…)` to match. The override stays because a rotated token is
     * still a good moment to re-assert the registration, which is keyed on the
     * FID and therefore unaffected by the rotation itself.
     */
    override fun onNewToken(token: String) {
        Log.i(TAG, "token rotated — re-registering device")
        RegisterDeviceWorker.enqueue(applicationContext)
    }

    private companion object { const val TAG = "SyncMessaging" }
}
