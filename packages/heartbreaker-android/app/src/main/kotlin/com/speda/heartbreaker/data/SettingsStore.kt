package com.speda.heartbreaker.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** The slice of store/settings.ts the shell needs (M3). Defaults mirror its DEFAULT. */
data class HbSettings(
    val model: String = DEFAULT_MODEL,
    val userName: String = "",
    val systemPrompt: String = "",
    val temperature: Float = 0.7f,
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
    }

    val settings: Flow<HbSettings> = context.settingsDataStore.data.map { p ->
        HbSettings(
            model = p[Keys.MODEL]?.ifEmpty { null } ?: DEFAULT_MODEL,
            userName = p[Keys.USER_NAME].orEmpty(),
            systemPrompt = p[Keys.SYSTEM_PROMPT].orEmpty(),
            temperature = p[Keys.TEMPERATURE]?.toFloatOrNull() ?: 0.7f,
        )
    }

    suspend fun setModel(model: String) = context.settingsDataStore.edit { it[Keys.MODEL] = model }.let { }
    suspend fun setUserName(name: String) = context.settingsDataStore.edit { it[Keys.USER_NAME] = name }.let { }
    suspend fun setSystemPrompt(prompt: String) = context.settingsDataStore.edit { it[Keys.SYSTEM_PROMPT] = prompt }.let { }
    suspend fun setTemperature(temp: Float) = context.settingsDataStore.edit { it[Keys.TEMPERATURE] = temp.toString() }.let { }
}
