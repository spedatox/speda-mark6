package com.speda.heartbreaker.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** The slice of store/settings.ts the shell needs (M3). Defaults mirror its DEFAULT. */
data class HbSettings(
    val model: String = DEFAULT_MODEL,
    val userName: String = "",
    val systemPrompt: String = "",
    val temperature: Float = 0.7f,
    /** Android-exclusive: share device location with SPEDA each turn (opt-in). */
    val locationEnabled: Boolean = false,
    /** Whether the first-launch location permission prompt has already fired. */
    val locationPrompted: Boolean = false,
    /** Atomix health sync — master switch (docs/ATOMIX_HEALTH_SYNC.md §1.1). */
    val healthEnabled: Boolean = false,
    /** Enabled record types, by HealthType.key. Empty set = the defaults. */
    val healthTypes: Set<String> = emptySet(),
    /** Health Connect differential token; blank forces a backfill. */
    val healthChangesToken: String = "",
    /** Epoch millis of the last successful ingest; 0 = never. */
    val healthLastSync: Long = 0L,
    val healthBackfillDone: Boolean = false,
    /** One-time "Atomix can read your health data" banner, per §1.1. */
    val healthNudgeSeen: Boolean = false,
)

/** store/settings.ts DEFAULT.model — the routing default until the owner picks. */
const val DEFAULT_MODEL = "claude-sonnet-4-6"

private val Context.settingsDataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

class SettingsStore(private val context: Context) {

    private object Keys {
        val MODEL = stringPreferencesKey("model")
        val USER_NAME = stringPreferencesKey("user_name")
        val SYSTEM_PROMPT = stringPreferencesKey("system_prompt")
        val TEMPERATURE = stringPreferencesKey("temperature")
        val LOCATION_ENABLED = booleanPreferencesKey("location_enabled")
        val LOCATION_PROMPTED = booleanPreferencesKey("location_prompted")
        val HEALTH_ENABLED = booleanPreferencesKey("health_enabled")
        val HEALTH_TYPES = stringSetPreferencesKey("health_types")
        val HEALTH_CHANGES_TOKEN = stringPreferencesKey("health_changes_token")
        val HEALTH_LAST_SYNC = longPreferencesKey("health_last_sync")
        val HEALTH_BACKFILL_DONE = booleanPreferencesKey("health_backfill_done")
        val HEALTH_NUDGE_SEEN = booleanPreferencesKey("health_nudge_seen")
    }

    val settings: Flow<HbSettings> = context.settingsDataStore.data.map { p ->
        HbSettings(
            model = p[Keys.MODEL]?.ifEmpty { null } ?: DEFAULT_MODEL,
            userName = p[Keys.USER_NAME].orEmpty(),
            systemPrompt = p[Keys.SYSTEM_PROMPT].orEmpty(),
            temperature = p[Keys.TEMPERATURE]?.toFloatOrNull() ?: 0.7f,
            locationEnabled = p[Keys.LOCATION_ENABLED] ?: false,
            locationPrompted = p[Keys.LOCATION_PROMPTED] ?: false,
            healthEnabled = p[Keys.HEALTH_ENABLED] ?: false,
            healthTypes = p[Keys.HEALTH_TYPES] ?: emptySet(),
            healthChangesToken = p[Keys.HEALTH_CHANGES_TOKEN].orEmpty(),
            healthLastSync = p[Keys.HEALTH_LAST_SYNC] ?: 0L,
            healthBackfillDone = p[Keys.HEALTH_BACKFILL_DONE] ?: false,
            healthNudgeSeen = p[Keys.HEALTH_NUDGE_SEEN] ?: false,
        )
    }

    suspend fun setModel(model: String) = context.settingsDataStore.edit { it[Keys.MODEL] = model }.let { }
    suspend fun setUserName(name: String) = context.settingsDataStore.edit { it[Keys.USER_NAME] = name }.let { }
    suspend fun setSystemPrompt(prompt: String) = context.settingsDataStore.edit { it[Keys.SYSTEM_PROMPT] = prompt }.let { }
    suspend fun setTemperature(temp: Float) = context.settingsDataStore.edit { it[Keys.TEMPERATURE] = temp.toString() }.let { }
    suspend fun setLocationEnabled(on: Boolean) = context.settingsDataStore.edit { it[Keys.LOCATION_ENABLED] = on }.let { }
    suspend fun setLocationPrompted(done: Boolean) = context.settingsDataStore.edit { it[Keys.LOCATION_PROMPTED] = done }.let { }

    // ── Atomix health sync ────────────────────────────────────────────────────

    suspend fun setHealthEnabled(on: Boolean) = context.settingsDataStore.edit { it[Keys.HEALTH_ENABLED] = on }.let { }
    suspend fun setHealthTypes(types: Set<String>) = context.settingsDataStore.edit { it[Keys.HEALTH_TYPES] = types }.let { }
    suspend fun setHealthChangesToken(token: String) = context.settingsDataStore.edit { it[Keys.HEALTH_CHANGES_TOKEN] = token }.let { }
    suspend fun setHealthLastSync(epochMillis: Long) = context.settingsDataStore.edit { it[Keys.HEALTH_LAST_SYNC] = epochMillis }.let { }
    suspend fun setHealthBackfillDone(done: Boolean) = context.settingsDataStore.edit { it[Keys.HEALTH_BACKFILL_DONE] = done }.let { }
    suspend fun setHealthNudgeSeen(seen: Boolean) = context.settingsDataStore.edit { it[Keys.HEALTH_NUDGE_SEEN] = seen }.let { }

    /** DISCONNECT + WIPE: forget the token and the backfill flag so a future
     *  re-enable starts clean, and drop the master switch. */
    suspend fun clearHealthSyncState() = context.settingsDataStore.edit {
        it[Keys.HEALTH_ENABLED] = false
        it[Keys.HEALTH_CHANGES_TOKEN] = ""
        it[Keys.HEALTH_LAST_SYNC] = 0L
        it[Keys.HEALTH_BACKFILL_DONE] = false
    }.let { }
}
