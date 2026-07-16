package com.speda.heartbreaker.domain

import kotlin.math.roundToInt

/**
 * Stream liveness watchdog — real status, not looped filler, and a hard stop if
 * the backend goes quiet. Port of the watchdog block in ChatMain.tsx. The
 * diagnostic strings are copied VERBATIM (the phase-specific reasons the message
 * shows on timeout).
 */
object Watchdog {
    const val STALL_MS = 15_000L  // no events this long → tell the user it's slow
    const val DEAD_MS = 300_000L  // no events this long → give up, precise reason
    const val TICK_MS = 1_000L

    /** The stall status line (idle ≥ STALL_MS, no tool running yet). */
    fun stallStatus(modelName: String, waitedS: Int): String =
        "Waiting on $modelName — ${waitedS}s, no tokens yet (may be rate-limited)"

    /**
     * The phase-specific timeout reason (idle ≥ DEAD_MS). Names the phase the turn
     * died in — a diagnostic, not "isn't responding".
     */
    fun timeoutReason(gotStart: Boolean, gotTool: Boolean, modelName: String, waitedS: Int): String = when {
        !gotStart ->
            "No response from the backend in ${waitedS}s — it never acknowledged the request. " +
                "The API server may be down, unreachable, or stuck before the model started."
        gotTool ->
            "A tool call ran ${waitedS}s with no further output, so the turn was cancelled — " +
                "the tool or a service it calls is likely stuck."
        else ->
            "$modelName accepted the request but streamed nothing for ${waitedS}s — almost always " +
                "rate-limited, overloaded, or queued upstream. Cancelled; try again in a moment."
    }

    /** Model label for the copy: last `:`-segment of the model ref, uppercased. */
    fun modelLabel(model: String?): String =
        if (model.isNullOrEmpty()) "the model" else (model.substringAfterLast(':').ifEmpty { model }).uppercase()

    fun elapsedSeconds(startedAtMs: Long, nowMs: Long): Int = ((nowMs - startedAtMs) / 1000.0).roundToInt()
}

/**
 * Typewriter reveal math — the adaptive exponential catch-up from Message.tsx.
 * Pure so it can be fixture-tested; the composable drives it with withFrameNanos.
 */
object Typewriter {
    const val FLOOR = 45.0        // chars/sec floor
    const val CATCH_UP = 7.0      // chars/sec per remaining char (exponential approach)
    const val MAX_DT = 0.05       // dt clamp (seconds)

    /**
     * Advance the revealed count by one frame. [dtSeconds] is clamped to [MAX_DT].
     * Returns the new (fractional) revealed position; when within 0.5 of [target]
     * it snaps to target.
     */
    fun advance(revealed: Double, target: Int, dtSeconds: Double): Double {
        val remaining = target - revealed
        if (remaining <= 0.5) return target.toDouble()
        val dt = if (dtSeconds > MAX_DT) MAX_DT else dtSeconds
        val speed = maxOf(FLOOR, remaining * CATCH_UP)
        return minOf(target.toDouble(), revealed + speed * dt)
    }
}
