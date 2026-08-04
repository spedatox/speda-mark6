package com.speda.heartbreaker.push

import android.content.Context
import android.os.Build
import android.util.Log
import com.google.firebase.FirebaseApp
import com.google.firebase.installations.FirebaseInstallations
import com.speda.heartbreaker.BuildConfig
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.UplinkState
import com.speda.heartbreaker.data.UplinkStore
import com.speda.heartbreaker.domain.AppConfig
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * Tells Igor where to push.
 *
 * Registration is by **Firebase Installation ID**, not a registration token:
 * firebase-messaging 25.1.x deprecated `getToken`/`onNewToken` outright and the
 * Admin SDKs moved to `Message(fid=…)` to match. Ultron Wear registers the same
 * way against the same `/devices/register` endpoint — `platform` is the only
 * thing separating the two, and it is what keeps a health-sync wake from
 * vibrating the watch.
 *
 * Everything here is best-effort and silent on failure. Push is a convenience
 * over the 15-minute demand poll, not a dependency of it: a build with no
 * google-services.json, a device with no Play Services, or a server that cannot
 * be reached must all leave the app working exactly as before.
 */
class PushRegistrar(
    private val context: Context,
    private val uplink: UplinkStore,
    private val api: IgorApi,
) {

    /** Register this installation with Igor, if push is available at all. */
    suspend fun register() {
        if (!BuildConfig.PUSH_ENABLED) {
            Log.d(TAG, "push disabled at build time (no google-services.json)")
            return
        }
        // Firebase auto-init reads google_app_id out of the generated resources.
        // Absent or malformed, getInstance() throws rather than returning null —
        // hence the check instead of a null test.
        if (FirebaseApp.getApps(context).isEmpty()) {
            Log.w(TAG, "Firebase did not initialise; push unavailable")
            return
        }

        val state = uplink.state.first()
        val configured = (state as? UplinkState.Configured)?.uplink ?: return
        val config = AppConfig(
            apiBase = configured.apiBase,
            apiKey = configured.apiKey,
            agentId = "atomix",
        )

        val fid = installationId() ?: return
        val ok = api.registerDevice(config, deviceId(), PLATFORM, fid)
        Log.i(TAG, if (ok) "registered for push" else "push registration rejected")
    }

    private suspend fun installationId(): String? = suspendCancellableCoroutine { cont ->
        runCatching {
            FirebaseInstallations.getInstance().id
                .addOnSuccessListener { cont.resume(it) }
                .addOnFailureListener {
                    Log.w(TAG, "could not read installation id: ${it.message}")
                    cont.resume(null)
                }
        }.onFailure {
            Log.w(TAG, "installations unavailable: ${it.message}")
            cont.resume(null)
        }
    }

    /**
     * Stable per install, and distinct from the watch's. Igor keys devices on
     * this, so it must not change between launches — model alone would collide
     * if the owner ever ran SPEDA GO on two identical handsets.
     */
    private fun deviceId(): String =
        "speda-go-${Build.MANUFACTURER}-${Build.MODEL}"
            .lowercase()
            .replace(Regex("[^a-z0-9-]+"), "-")
            .trim('-')
            .take(64)

    private companion object {
        const val TAG = "SpedaPush"
        // NOT "wear" — the academic attendance push targets that platform, and
        // a phone answering an attendance ask would be a silent wrong answer.
        const val PLATFORM = "phone"
    }
}
