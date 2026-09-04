// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.health

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.spedatox.atomixwear.AtomixWear
import com.spedatox.atomixwear.sync.SyncScheduler
import kotlinx.coroutines.launch

/**
 * Re-arms collection after a reboot.
 *
 * Health Services does not restore a passive listener registration across a
 * device restart, and WorkManager's schedule needs re-asserting too. Without
 * this the watch silently stops collecting after every reboot — the worst class
 * of bug for this application, because nothing appears broken until someone
 * notices a gap in the data days later.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        Log.i(TAG, "boot completed — re-arming collection")

        val app = context.applicationContext as AtomixWear
        // goAsync() so the process is not eligible for termination between the
        // broadcast and the registration actually landing.
        val pending = goAsync()
        app.appScope.launch {
            try {
                app.biometrics.register()
                SyncScheduler.ensureScheduled(app)
            } finally {
                pending.finish()
            }
        }
    }

    private companion object { const val TAG = "BootReceiver" }
}
