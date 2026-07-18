package com.speda.heartbreaker.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * Wire DTOs for the settings surface — mirrors of the interfaces in lib/api.ts
 * used by SettingsModal.tsx and ConfigTab.tsx.
 */

/** One MCP server row (GET /connections). */
@Serializable
data class ConnectionInfo(
    val server: String,
    val label: String = "",
    val connected: Boolean = false,
    val active: Boolean = false,
    val tools: Int = 0,
    @SerialName("always_on") val alwaysOn: Boolean = false,
    val needs: String? = null,
)

/** GET /connections — server list plus the prompt-prefix token budget. */
@Serializable
data class ConnectionsResult(
    val servers: List<ConnectionInfo> = emptyList(),
    @SerialName("active_tool_tokens") val activeToolTokens: Int = 0,
    @SerialName("itpm_limit") val itpmLimit: Int = 30000,
)

/** GET /automations — one proactive n8n watcher. */
@Serializable
data class AutomationInfo(
    val id: Int,
    val name: String = "",
    val kind: String = "",
    val intent: String = "",
    val active: Boolean = true,
    val summary: String = "",
    @SerialName("last_fired_at") val lastFiredAt: String? = null,
    @SerialName("expires_at") val expiresAt: String? = null,
)

/** GET /automations/status — the n8n + Telegram pipeline health. */
@Serializable
data class AutomationsStatus(
    @SerialName("n8n_configured") val n8nConfigured: Boolean = false,
    @SerialName("n8n_online") val n8nOnline: Boolean = false,
    @SerialName("n8n_url") val n8nUrl: String = "",
    @SerialName("telegram_configured") val telegramConfigured: Boolean = false,
    @SerialName("telegram_connected") val telegramConnected: Boolean = false,
)

/** One editable backend setting (GET /config groups → fields). */
@Serializable
data class ConfigFieldInfo(
    val key: String,
    val label: String = "",
    val type: String = "text",           // text | password | bool | int | select | url
    val secret: Boolean = false,
    @SerialName("requires_restart") val requiresRestart: Boolean = false,
    val help: String = "",
    val placeholder: String = "",
    val options: List<String> = emptyList(),
    @SerialName("is_set") val isSet: Boolean = false,
    /** Present for non-secret fields; string/number/bool decoded per [type]. */
    val value: JsonElement? = null,
    /** Masked hint for secret fields (e.g. "sk-…abcd"). */
    val hint: String? = null,
)

@Serializable
data class ConfigGroupInfo(
    val id: String,
    val label: String = "",
    val blurb: String = "",
    val fields: List<ConfigFieldInfo> = emptyList(),
)

@Serializable
data class ConfigSaveResult(
    @SerialName("applied_live") val appliedLive: List<String> = emptyList(),
    @SerialName("restart_required") val restartRequired: List<String> = emptyList(),
    val rejected: List<String> = emptyList(),
)

/** Per-agent source-of-truth memory file assignment. */
@Serializable
data class SourceAgentInfo(
    @SerialName("agent_id") val agentId: String,
    val name: String = "",
    val domain: String = "",
    val source: String? = null,
    val default: String? = null,
)

@Serializable
data class MemorySources(
    val files: List<String> = emptyList(),
    val agents: List<SourceAgentInfo> = emptyList(),
)
