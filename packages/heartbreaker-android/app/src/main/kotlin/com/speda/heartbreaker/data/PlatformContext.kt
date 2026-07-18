package com.speda.heartbreaker.data

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Geocoder
import android.location.Location
import android.location.LocationManager
import android.os.Build
import androidx.core.content.ContextCompat
import com.speda.heartbreaker.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import java.util.Locale

/**
 * Ambient client context sent with each turn so SPEDA is platform- and
 * location-aware (mirrors ChatRequest.client_context on the backend). Platform
 * fields are always present; [location] only when the owner has enabled sharing
 * and granted the permission.
 */
@Serializable
data class ClientContext(
    val platform: String,
    val device: String,
    val osVersion: String,
    val appVersion: String,
    val locale: String,
    val location: ClientLocation? = null,
)

@Serializable
data class ClientLocation(
    val lat: Double,
    val lng: Double,
    val accuracyM: Float? = null,
    val place: String? = null,
)

/**
 * Assembles [ClientContext]. Static device facts are free; [snapshot] optionally
 * reads the last known position from the platform [LocationManager] (no Google
 * Play Services dependency) and reverse-geocodes it to a place label.
 */
class PlatformContextProvider(context: Context) {

    private val appContext = context.applicationContext

    private val staticDevice: String =
        listOf(Build.MANUFACTURER, Build.MODEL)
            .filter { it.isNotBlank() }
            .joinToString(" ")
            .replaceFirstChar { if (it.isLowerCase()) it.titlecase(Locale.US) else it.toString() }

    private val osVersion: String = "Android ${Build.VERSION.RELEASE}"

    /**
     * A fresh context snapshot. [includeLocation] gates the (permission-guarded)
     * position read; when false, or when the permission isn't granted, or when no
     * fix is available, [ClientContext.location] is null and only platform facts
     * are sent.
     */
    suspend fun snapshot(includeLocation: Boolean): ClientContext = ClientContext(
        platform = "android",
        device = staticDevice.ifBlank { "Android device" },
        osVersion = osVersion,
        appVersion = BuildConfig.VERSION_NAME,
        locale = Locale.getDefault().toLanguageTag(),
        location = if (includeLocation && hasLocationPermission()) currentLocation() else null,
    )

    fun hasLocationPermission(): Boolean =
        ContextCompat.checkSelfPermission(appContext, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(appContext, Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    private suspend fun currentLocation(): ClientLocation? = withContext(Dispatchers.IO) {
        val lm = appContext.getSystemService(Context.LOCATION_SERVICE) as? LocationManager ?: return@withContext null
        val best = runCatching { bestLastKnown(lm) }.getOrNull() ?: return@withContext null
        ClientLocation(
            lat = best.latitude,
            lng = best.longitude,
            accuracyM = if (best.hasAccuracy()) best.accuracy else null,
            place = reverseGeocode(best.latitude, best.longitude),
        )
    }

    /** Freshest fix across the enabled providers (GPS tends to be most recent). */
    private fun bestLastKnown(lm: LocationManager): Location? {
        val providers = try { lm.getProviders(true) } catch (_: SecurityException) { emptyList() }
        var best: Location? = null
        for (p in providers) {
            val loc = try { lm.getLastKnownLocation(p) } catch (_: SecurityException) { null } ?: continue
            if (best == null || loc.time > best!!.time) best = loc
        }
        return best
    }

    /** "Neighbourhood, City" from the platform Geocoder; null if it can't resolve. */
    @Suppress("DEPRECATION") // the async Geocoder overload is API 33+; this covers 31/32
    private fun reverseGeocode(lat: Double, lng: Double): String? = runCatching {
        val geocoder = Geocoder(appContext, Locale.getDefault())
        val results = geocoder.getFromLocation(lat, lng, 1)
        val a = results?.firstOrNull() ?: return null
        listOfNotNull(
            a.subLocality ?: a.locality ?: a.subAdminArea,
            a.adminArea,
            a.countryName,
        ).distinct().take(3).joinToString(", ").ifBlank { null }
    }.getOrNull()
}
