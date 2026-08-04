package com.speda.heartbreaker.push

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.speda.heartbreaker.HeartbreakerApp

/**
 * Igor's wake channel into SPEDA GO.
 *
 * Atomix will not write a health briefing from stale biometrics — a resting
 * heart rate from four days ago reported under today's date is a false
 * statement about the owner's body, not merely old news. So when a turn needs
 * data that describes the present, Igor pushes here and the app syncs Health
 * Connect on the spot instead of waiting for its next four-hourly trickle.
 *
 * **Data-only messages, always.** A message carrying a `notification` block is
 * rendered by the system tray when the app is backgrounded and this handler is
 * never called — which for a sync wake would mean a notification the owner has
 * to tap, for work they never asked to see. `services/fcm.py` sends data-only
 * with `android.priority = high` for exactly this reason (docs/ULTRON_WEAR.md).
 *
 * The token callback is deliberately not overridden: firebase-messaging 25.1.x
 * deprecated that whole API in favour of Firebase Installation IDs, which
 * [PushRegistrar] reads directly. See the FID note in docs/ULTRON_WEAR.md §3.
 */
class SpedaMessagingService : FirebaseMessagingService() {

    override fun onMessageReceived(message: RemoteMessage) {
        val graph = (applicationContext as? HeartbreakerApp)?.graph ?: return
        when (val type = message.data["type"]) {
            TYPE_HEALTH_SYNC -> {
                Log.i(TAG, "health sync demanded by Igor")
                graph.healthSync.syncNow()
            }
            // Unknown types are not an error: Igor and the app ship separately,
            // so a newer backend WILL send message types this build predates.
            // Logging and ignoring is what keeps that a no-op instead of a crash.
            else -> Log.d(TAG, "ignoring unknown push type: $type")
        }
    }

    private companion object {
        const val TAG = "SpedaPush"
        const val TYPE_HEALTH_SYNC = "health_sync_now"
    }
}
