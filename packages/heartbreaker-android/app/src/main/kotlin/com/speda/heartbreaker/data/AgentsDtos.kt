package com.speda.heartbreaker.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Wire DTOs for the multi-agent surfaces — mirrors of the interfaces in
 * lib/api.ts used by CommsTray.tsx, SystemsBoard.tsx and AgentModelPicker.tsx.
 */

/** One inter-agent dispatch/reply row (GET /agents/comms) — the AGENT_COMMS feed. */
@Serializable
data class AgentCommEntry(
    val id: Int,
    @SerialName("request_id") val requestId: String = "",
    @SerialName("from_agent") val fromAgent: String,
    @SerialName("to_agent") val toAgent: String,
    val kind: String = "dispatch",          // dispatch | broadcast
    val protocol: String = "direct",        // direct | house_party
    val task: String = "",
    val result: String? = null,
    val status: String = "running",         // running | ok | error | timeout | offline | refused
    @SerialName("duration_ms") val durationMs: Int? = null,
    @SerialName("created_at") val createdAt: String,
)

/** Per-agent model routing pin (GET/POST /agents/models) — Systems board AGENT CORES. */
@Serializable
data class AgentModelInfo(
    @SerialName("agent_id") val agentId: String,
    val name: String = "",
    val domain: String = "",
    val override: String? = null,
    @SerialName("telegram_override") val telegramOverride: String? = null,
    @SerialName("default_main") val defaultMain: String = "",
    @SerialName("default_background") val defaultBackground: String = "",
)

/** SPEDA's knowledge bank file (GET/PUT /memory/files) — Systems board DATA_BANKS. */
@Serializable
data class MemoryFileInfo(
    val path: String,
    val content: String = "",
    @SerialName("updated_at") val updatedAt: String? = null,
    /** Canonical files are owner-editable from the board; system trails are not. */
    val editable: Boolean = false,
)

/** One committed revision of a memory file (GET /memory/files/revisions). */
@Serializable
data class MemoryRevisionInfo(
    val id: Int,
    val path: String,
    val author: String = "",
    val action: String = "",
    @SerialName("created_at") val createdAt: String? = null,
    val before: String = "",
    val after: String = "",
)

/** Result of PUT /memory/files — success, an owner/agent write race (409), or failure. */
sealed interface MemoryCommitResult {
    data class Ok(val file: MemoryFileInfo) : MemoryCommitResult
    data class Conflict(val current: MemoryFileInfo?) : MemoryCommitResult
    data object Failed : MemoryCommitResult
}

/** An external peer (the Forge/Optimus) currently connected over /agents/ws/<id>
 *  (GET /agents). An agent absent from this list is answering in-process. */
@Serializable
data class OnlineAgent(
    @SerialName("agent_id") val agentId: String,
    @SerialName("agent_name") val agentName: String = "",
    val domain: String = "",
    val status: String = "",
    @SerialName("last_seen") val lastSeen: String? = null,
    val capabilities: List<String> = emptyList(),
)
