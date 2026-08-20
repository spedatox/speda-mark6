package com.speda.heartbreaker.domain

import com.speda.heartbreaker.i18n.AppStrings
import kotlin.math.roundToInt

/**
 * Stream liveness watchdog — real status, not looped filler, and a hard stop if
 * the backend goes quiet. Port of the watchdog block in ChatMain.tsx. The
 * diagnostic strings mirror `chatMain` in the i18n dict — this object is plain
 * Kotlin (no Compose), so callers hand it the resolved [AppStrings] rather than
 * it reading `LocalStrings` itself.
 */
object Watchdog {
    const val STALL_MS = 15_000L  // no events this long → tell the user it's slow
    const val DEAD_MS = 300_000L  // no events this long → give up, precise reason
    const val TICK_MS = 1_000L

    /** The stall status line (idle ≥ STALL_MS, no tool running yet). */
    fun stallStatus(modelName: String, waitedS: Int, t: AppStrings): String =
        t.chatMain.waitingOnModel(modelName, waitedS)

    /**
     * The phase-specific timeout reason (idle ≥ DEAD_MS). Names the phase the turn
     * died in — a diagnostic, not "isn't responding".
     */
    fun timeoutReason(gotStart: Boolean, gotTool: Boolean, modelName: String, waitedS: Int, t: AppStrings): String = when {
        !gotStart -> t.chatMain.timeoutNoAck(waitedS)
        gotTool -> t.chatMain.timeoutToolStuck(waitedS)
        else -> t.chatMain.timeoutNoStream(modelName, waitedS)
    }

    /** Model label for the copy: last `:`-segment of the model ref, uppercased. */
    fun modelLabel(model: String?, t: AppStrings): String =
        if (model.isNullOrEmpty()) t.chatMain.modelFallback else (model.substringAfterLast(':').ifEmpty { model }).uppercase()

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
