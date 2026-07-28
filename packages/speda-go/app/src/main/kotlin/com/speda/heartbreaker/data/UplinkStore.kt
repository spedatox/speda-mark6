package com.speda.heartbreaker.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** The backend connection — apiBase is not secret; apiKey is Keystore-wrapped. */
data class Uplink(val apiBase: String, val apiKey: String)

/** Loading-aware view of the stored uplink for first-run routing. */
sealed interface UplinkState {
    data object Unconfigured : UplinkState
    data class Configured(val uplink: Uplink) : UplinkState
}

private val Context.uplinkDataStore: DataStore<Preferences> by preferencesDataStore(name = "uplink")

/**
 * First-run configuration store — replaces the Electron env config. The apiBase
 * host is stored plaintext (it is not a secret); the API key is encrypted at rest
 * via [KeystoreCrypto] and only decrypted in memory when read.
 */
class UplinkStore(private val context: Context) {

    private object Keys {
        val API_BASE = stringPreferencesKey("api_base")
        val API_KEY_ENC = stringPreferencesKey("api_key_enc")
    }

    val state: Flow<UplinkState> = context.uplinkDataStore.data.map { prefs ->
        val base = prefs[Keys.API_BASE]?.trim().orEmpty()
        if (base.isEmpty()) {
            UplinkState.Unconfigured
        } else {
            val key = prefs[Keys.API_KEY_ENC]?.let(KeystoreCrypto::decrypt).orEmpty()
            UplinkState.Configured(Uplink(apiBase = base, apiKey = key))
        }
    }

    suspend fun save(apiBase: String, apiKey: String) {
        val normalizedBase = apiBase.trim().trimEnd('/')
        context.uplinkDataStore.edit { prefs ->
            prefs[Keys.API_BASE] = normalizedBase
            prefs[Keys.API_KEY_ENC] = KeystoreCrypto.encrypt(apiKey)
        }
    }

    suspend fun clear() {
        context.uplinkDataStore.edit { it.clear() }
    }
}
