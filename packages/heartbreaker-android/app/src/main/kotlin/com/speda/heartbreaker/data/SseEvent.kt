package com.speda.heartbreaker.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull

/**
 * One Server-Sent Event from the chat stream — mirror of SSEEvent in lib/types.ts.
 * `data` stays a raw [JsonElement], mapped per `type` exactly like the TS switch
 * (chunk → string, tool → ToolBadge, file → FileMeta, tool_result → {id,result}).
 */
@Serializable
data class SseEvent(
    val type: String,
    val data: JsonElement = JsonNull,
    @SerialName("session_id") val sessionId: Int = 0,
    @SerialName("request_id") val requestId: String = "",
)

/** Detached turns the backend is currently running (lib/api ActiveRun). */
@Serializable
data class ActiveRun(
    @SerialName("request_id") val requestId: String,
    @SerialName("agent_id") val agentId: String = "",
    @SerialName("session_id") val sessionId: Int = 0,
    @SerialName("running_s") val runningS: Double = 0.0,
    @SerialName("idle_s") val idleS: Double = 0.0,
)
