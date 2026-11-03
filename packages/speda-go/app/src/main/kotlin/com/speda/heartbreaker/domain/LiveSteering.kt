package com.speda.heartbreaker.domain

import kotlinx.coroutines.*
import java.util.UUID

data class SteeringTarget(val config: AppConfig, val requestId: String, val sessionId: Int?, val stepId: String?, val view: Long)

/** An ambiguous acknowledgment retains its original input ID and expected speaking step. */
class LiveSteering(
    private val scope: CoroutineScope,
    private val target: () -> SteeringTarget?,
    private val sameView: (SteeringTarget) -> Boolean,
    private val send: suspend (SteeringTarget, String, String) -> Boolean,
    private val accepted: (SteeringTarget, String, String) -> Unit,
    private val notice: (String?) -> Unit,
) {
    private data class Submission(val target: SteeringTarget, val text: String, val id: String, var job: Deferred<Boolean>? = null)
    private var pending: Submission? = null
    fun reset() { pending = null }

    suspend fun submit(text: String): Boolean {
        val source = target() ?: return false
        if (text.isBlank()) return false
        val item = pending?.takeIf { it.target == source && it.text == text }
            ?: Submission(source, text, UUID.randomUUID().toString()).also { pending = it }
        item.job?.let { return it.await() }
        val request = scope.async {
            try {
                val result = send(source, text, item.id)
                if (result) {
                    if (sameView(source)) { accepted(source, text, item.id); notice(null) }
                    if (pending === item) pending = null
                } else if (sameView(source)) notice("Instruction was not confirmed. Your draft is retained.")
                result
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (sameView(source)) notice(error.message ?: "Instruction was not confirmed."); false }
            finally { item.job = null }
        }
        item.job = request
        return request.await()
    }
}
