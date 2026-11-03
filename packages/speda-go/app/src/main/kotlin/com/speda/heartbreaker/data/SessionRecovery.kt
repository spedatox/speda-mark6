package com.speda.heartbreaker.data

import com.speda.heartbreaker.domain.*
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.*
import java.io.IOException

class ApiFailure(message: String, val retryable: Boolean) : IOException(message)

/** Handles fragmented/multiline SSE, rejects corrupt and oversized envelopes explicitly. */
class SseDecoder {
    private val data = StringBuilder()
    private var bytes = 0
    private val json = Json { ignoreUnknownKeys = true }
    fun line(line: String): SseEvent? {
        if (line.isEmpty()) return finish()
        if (!line.startsWith("data:")) return null
        bytes += line.substring(5).toByteArray(Charsets.UTF_8).size + 1
        if (bytes > 256_000) throw ApiFailure("Stream frame exceeds the recovery limit", false)
        if (data.isNotEmpty()) data.append('\n')
        data.append(line.substring(5).removePrefix(" "))
        if (data.length > 256_000) throw ApiFailure("Stream frame exceeds the recovery limit", false)
        return null
    }
    fun finish(): SseEvent? {
        if (data.isEmpty()) return null
        val raw = data.toString(); data.clear(); bytes = 0
        return try { json.decodeFromString<SseEvent>(raw) }
        catch (error: Exception) { throw ApiFailure("Malformed stream frame", false) }
    }
}

data class WorkerCard(val parentStep: String, val parentMessage: String?, val run: SubagentRun)
data class WorkerSessionSnapshot(val sessionId: Int, val sequence: Long, val omitted: Int, val cards: List<WorkerCard>)

fun parseWorkerSnapshot(element: JsonElement, sessionId: Int): WorkerSessionSnapshot {
    fun invalid(): Nothing = throw ApiFailure("Worker recovery returned an invalid snapshot", false)
    val body = element as? JsonObject ?: invalid()
    if ((body["session_id"] as? JsonPrimitive)?.intOrNull != sessionId) invalid()
    val sequence = (body["sequence"] as? JsonPrimitive)?.longOrNull?.takeIf { it >= 0 } ?: invalid()
    val cards = body["cards"] as? JsonArray ?: invalid()
    if (cards.size > 64) invalid()
    return WorkerSessionSnapshot(sessionId, sequence, (body["omitted"] as? JsonPrimitive)?.intOrNull ?: 0, cards.map {
        val card = it as? JsonObject ?: invalid()
        val step = card.roomText("parent_step_id")?.takeIf { id -> id.matches(Regex("[a-f0-9]{32}")) } ?: invalid()
        val raw = card["run"] as? JsonObject ?: invalid()
        val run = MessageJson.parseSubagent(raw) ?: invalid()
        if (!(run.executionId ?: run.id).matches(Regex("[a-f0-9]{32}")) || (run.lastSequence ?: -1) < 0 || raw["steps"] !is JsonArray ||
            raw.roomText("agent") == null || raw.roomText("label") == null) invalid()
        WorkerCard(step, card.roomText("parent_message_id"), boundedWorkerRun(run))
    })
}

/** Snapshot before subscribing; recovery performs reads only and outlives the public reply. */
suspend fun watchSessionWorkers(
    snapshot: suspend () -> WorkerSessionSnapshot,
    attach: (Long) -> Flow<SseEvent>,
    current: () -> Boolean,
    onCard: (WorkerCard) -> Unit,
    onStatus: (String?) -> Unit,
) {
    var backoff = 1000L
    val cards = linkedMapOf<String, WorkerCard>()
    while (currentCoroutineContext().isActive && current()) {
        try {
            val recovered = snapshot()
            if (!current()) return
            recovered.cards.forEach { card ->
                val id = card.run.executionId ?: card.run.id
                cards[id] = card.copy(run = boundedWorkerRun(mergeWorkerRun(cards[id]?.run, card.run)))
                onCard(cards.getValue(id))
                if (cards.size > 128) cards.remove(cards.keys.first())
            }
            var cursor = recovered.sequence
            onStatus(if (recovered.omitted > 0) "Some older assignments are omitted from this preview." else null)
            attach(cursor).collect { envelope ->
                if (!current()) return@collect
                if (envelope.type != "subagent") throw ApiFailure("Invalid worker stream envelope", false)
                val event = envelope.data as? JsonObject ?: throw ApiFailure("Invalid worker stream event", false)
                val id = event.roomText("execution_id") ?: event.roomText("id").orEmpty()
                val step = event.roomText("parent_step_id") ?: cards[id]?.parentStep.orEmpty()
                val sequence = (event["sequence"] as? JsonPrimitive)?.longOrNull ?: 0
                if (!id.matches(Regex("[a-f0-9]{32}")) || !step.matches(Regex("[a-f0-9]{32}")) || sequence <= 0)
                    throw ApiFailure("Worker activity has invalid execution identity", false)
                if (sequence <= cursor) return@collect
                val prior = cards[id]
                val next = WorkerCard(step, prior?.parentMessage, boundedWorkerRun(foldSubagentEvent(prior?.run, event)))
                cards.remove(id); cards[id] = next
                if (cards.size > 128) cards.remove(cards.keys.first())
                onCard(next)
                cursor = sequence
                backoff = 1000
            }
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) {
            if (!current()) return
            if (error is ApiFailure && !error.retryable) { onStatus(error.message); return }
        }
        if (!current()) return
        onStatus("Worker connection interrupted; recovering recorded activity.")
        delay(backoff)
        backoff = (backoff * 2).coerceAtMost(15000)
    }
}
