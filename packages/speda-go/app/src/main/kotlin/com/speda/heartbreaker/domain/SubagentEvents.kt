package com.speda.heartbreaker.domain

import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toPersistentList
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull

private fun JsonObject.workerText(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull

/** Native sequence/event identity makes durable SUBAGENT replay idempotent. */
fun foldSubagentEvent(previous: SubagentRun?, event: JsonObject): SubagentRun {
    val sequence = (event["sequence"] as? JsonPrimitive)?.longOrNull?.takeIf { it > 0 }
    val eventId = event.workerText("event_id")
    if (previous != null && ((sequence != null && sequence <= (previous.lastSequence ?: 0)) ||
                (eventId != null && eventId in previous.seenEventIds))) return previous
    var run = previous ?: SubagentRun(id = event.workerText("id") ?: event.workerText("execution_id") ?: "")
    val status = event.workerText("status") ?: run.status
    run = run.copy(
        agent = event.workerText("agent") ?: run.agent,
        label = event.workerText("label") ?: run.label,
        source = event.workerText("source") ?: run.source,
        executionId = event.workerText("execution_id") ?: run.executionId,
        ownerAgent = event.workerText("owner_agent") ?: run.ownerAgent,
        workerRole = event.workerText("role") ?: run.workerRole,
        executionType = event.workerText("execution_type") ?: run.executionType,
        status = status,
        lastSequence = sequence ?: run.lastSequence,
        seenEventIds = if (eventId != null) (run.seenEventIds + eventId).toPersistentList() else run.seenEventIds,
    )
    when (event.workerText("phase")) {
        "started" -> run = run.copy(prompt = event.workerText("prompt") ?: run.prompt, running = true)
        "text" -> event.workerText("text")?.takeIf { it.isNotEmpty() }?.let { text ->
            val last = run.steps.lastOrNull()
            run = if (last?.kind == "text") run.copy(steps = run.steps.set(run.steps.lastIndex, last.copy(text = (last.text ?: "") + text)))
                else run.copy(steps = (run.steps + SubagentStep(kind = "text", text = text)).toPersistentList())
        }
        "tool" -> event.workerText("tool_call_id")?.let { callId ->
            val index = run.steps.indexOfFirst { it.kind == "tool" && it.toolCallId == callId }
            val step = SubagentStep(kind = "tool", tool = event.workerText("tool"), input = event["input"], toolCallId = callId,
                result = if (index >= 0) run.steps[index].result else null)
            run = run.copy(steps = if (index >= 0) run.steps.set(index, step) else (run.steps + step).toPersistentList())
        }
        "tool_result" -> event.workerText("tool_call_id")?.let { callId ->
            val index = run.steps.indexOfFirst { it.kind == "tool" && it.toolCallId == callId }
            val result = event.workerText("error") ?: event.workerText("result")
            run = run.copy(steps = if (index >= 0) run.steps.set(index, run.steps[index].copy(result = result))
                else (run.steps + SubagentStep(kind = "tool", tool = event.workerText("tool"), toolCallId = callId, result = result)).toPersistentList())
        }
        "finished" -> run = run.copy(running = false,
            ok = (event["ok"] as? JsonPrimitive)?.booleanOrNull ?: (status == null || status in setOf("succeeded", "completed", "ok")),
            report = event.workerText("report") ?: run.report)
    }
    if (status in setOf("queued", "running", "staging", "waiting_input", "waiting_dependency")) run = run.copy(running = true)
    if (event.workerText("phase") == "executor_unavailable") run = run.copy(running = false, status = "unknown", ok = null)
    if (status in setOf("unknown", "succeeded", "completed", "failed", "cancelled", "interrupted", "expired", "reconciling", "blocked")) run = run.copy(running = false)
    return boundedWorkerRun(run)
}

fun workerLabel(run: SubagentRun): String {
    val role = (run.workerRole ?: run.agent).removePrefix("forge_")
    val name = when {
        role in setOf("autobot", "coder") -> "Autobot"
        role in setOf("decepticon", "pentester") -> "Decepticon"
        run.source == "forge" || run.executionType == "forge" -> "Forge · $role"
        run.source == "legion" || run.executionType == "legion" -> "Legion · $role"
        else -> role
    }
    val owner = run.ownerAgent?.let { if (it == "nightcrawler" || it == "friday") "Friday" else it.replaceFirstChar { char -> char.uppercase() } }
    return if (owner.isNullOrEmpty()) name else "$name · supervised by $owner"
}
