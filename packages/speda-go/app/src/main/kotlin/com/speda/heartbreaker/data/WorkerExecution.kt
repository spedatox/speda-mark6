package com.speda.heartbreaker.data

import com.speda.heartbreaker.domain.SubagentRun
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

@Serializable
data class WorkerExecution(
    @SerialName("execution_id") val executionId: String,
    @SerialName("owner_agent") val ownerAgent: String? = null,
    @SerialName("agent_id") val agentId: String? = null,
    val role: String? = null,
    val worker: String? = null,
    val task: String = "",
    val label: String? = null,
    val status: String = "unknown",
    val backend: String? = null,
    @SerialName("execution_type") val executionType: String? = null,
    @SerialName("last_sequence") val lastSequence: Long? = null,
    val model: String? = null,
    val result: JsonElement? = null,
    @SerialName("can_interrupt") val canInterrupt: Boolean? = null,
    @SerialName("can_message") val canMessage: Boolean? = null,
) {
    fun toRun() = SubagentRun(id = executionId, executionId = executionId,
        agent = role ?: worker ?: "worker", workerRole = role ?: worker, label = label ?: task,
        ownerAgent = ownerAgent ?: agentId, executionType = executionType ?: backend,
        source = if ((executionType ?: backend) == "forge") "forge" else "legion",
        report = (result as? JsonPrimitive)?.contentOrNull ?: ((result as? JsonObject)?.get("report") as? JsonPrimitive)?.contentOrNull ?: ((result as? JsonObject)?.get("reason") as? JsonPrimitive)?.contentOrNull,
        model = model, status = status, canInterrupt = canInterrupt, canMessage = canMessage,
        running = status in setOf("queued", "running", "staging", "waiting_input", "waiting_dependency"))
}
