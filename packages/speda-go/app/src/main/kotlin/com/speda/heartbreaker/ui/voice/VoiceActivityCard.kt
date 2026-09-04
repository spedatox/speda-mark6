// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.voice

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.ToolBadge
import com.speda.heartbreaker.domain.ToolStatus
import com.speda.heartbreaker.domain.ToolStepState
import com.speda.heartbreaker.domain.inputRows
import com.speda.heartbreaker.domain.resultSummary
import com.speda.heartbreaker.domain.stepState
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.prose.ChartPanel
import kotlinx.coroutines.delay

/**
 * What the machine is doing, while it is doing it — voice mode's first window.
 *
 * ── The problem it exists for ───────────────────────────────────────────────
 * Voice mode showed a glowing "thinking" line and nothing else. A turn that was
 * browsing six pages and a turn that had hung looked identical for two minutes,
 * so the mode read as slow when it was working hard. The information was there
 * the whole time — the backend streams every tool call, with its arguments,
 * BEFORE it executes — voice mode just threw it away.
 *
 * ── Why it is louder than the transcript's version ──────────────────────────
 * In chat the step list is a collapsed receipt: the answer is already on screen
 * and the steps are for when something looks wrong. Here there IS no answer yet
 * and the owner is listening rather than reading, so the same data has the
 * opposite job — it is the only evidence anything is happening. Hence: no
 * collapse, arguments shown inline rather than behind a tap, and a LIVE timer
 * per step, which the transcript does not have at all.
 *
 * The timer is the part that actually answers "is this broken?". A counter
 * ticking past twelve seconds on a page fetch is a slow website; the same screen
 * without one is indistinguishable from a crash.
 */
@Composable
fun VoiceActivityCard(
    tools: List<ToolBadge>,
    /** True while the turn is still being written — the last step is only
     *  "running" while that holds, or a finished turn spins for ever. */
    streaming: Boolean,
    /** Whether any narration has arrived. Before it has, this card is the whole
     *  answer to "is anything happening", and says so in as many words. */
    hasText: Boolean,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current

    // When each step started and finished. Measured here because the events
    // carry no timestamps — a tool arrives when it starts and is mutated in
    // place when its result lands. Wall time is the right clock anyway: what the
    // owner wants to know is how long THEY have been waiting.
    val starts = remember { mutableMapOf<String, Long>() }
    val ends = remember { mutableMapOf<String, Long>() }
    val turnStart = remember { System.currentTimeMillis() }

    val liveIdx = if (streaming) tools.lastIndex else -1
    val running = streaming && (tools.isEmpty() || tools.last().result == null)

    // Re-composition tick, so the counters move. Only while something is in
    // flight: a permanent timer behind a finished board would redraw for ever to
    // animate nothing.
    var tick by remember { mutableIntStateOf(0) }
    LaunchedEffect(running) {
        while (running) {
            delay(100)
            tick++
        }
    }

    // Keyed on `tick` so the clock is genuinely re-read each time the timer
    // fires — Compose skips work whose inputs have not changed, and
    // currentTimeMillis() is not an input to anything.
    val now = remember(tick) { System.currentTimeMillis() }
    for (tool in tools) {
        starts.getOrPut(tool.id) { now }
        if (tool.result != null) ends.getOrPut(tool.id) { now }
    }

    ChartPanel(title = "ACTIVITY_LIVE", modifier = modifier) {
        Column(Modifier.padding(horizontal = 12.dp)) {

            // The turn's own clock — a different question from the per-step
            // ones: not "is this step slow" but "how long have I been waiting".
            Row(
                Modifier.fillMaxWidth().padding(bottom = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                BasicLabel(
                    "${tools.size} " +
                        if (tools.size == 1) t.voiceActivity.step else t.voiceActivity.steps,
                    palette.textFaint,
                )
                Box(Modifier.weight(1f))
                if (running) {
                    BasicLabel(elapsed(now - turnStart), palette.accent)
                }
            }

            // Before the first tool and before the first word, this line is the
            // whole signal that the machine is alive. Its absence is what made
            // the mode feel broken.
            if (tools.isEmpty()) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(9.dp),
                ) {
                    Spinner(palette.accent)
                    BasicText(
                        AnnotatedString(
                            if (hasText) t.voiceActivity.composing else t.voiceActivity.working,
                        ),
                        style = HbType.read.copy(fontSize = 13.sp, color = palette.textDim),
                    )
                }
            }

            tools.forEachIndexed { i, tool ->
                val state = tool.stepState(i == liveIdx)
                val took = (ends[tool.id] ?: now) - (starts[tool.id] ?: now)
                if (i > 0) {
                    Box(Modifier.fillMaxWidth().height(1.dp).background(palette.line))
                }
                Column(Modifier.padding(vertical = 6.dp)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(9.dp),
                    ) {
                        when (state) {
                            ToolStepState.Running -> Spinner(palette.accent)
                            ToolStepState.Failed -> Mark(palette.red)
                            ToolStepState.Done -> Mark(palette.green)
                        }
                        BasicText(
                            AnnotatedString(ToolStatus.statusLabel(tool.name, t)),
                            style = HbType.read.copy(fontSize = 13.sp, color = palette.text),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f),
                        )
                        BasicLabel(
                            elapsed(took),
                            if (state == ToolStepState.Running) palette.accent else palette.textFaint,
                        )
                    }

                    // The arguments, inline. In chat these sit behind a tap
                    // because the answer is already on screen; here there is
                    // nothing else to look at, and "which page is it reading" is
                    // exactly the question.
                    val args = tool.inputRows()
                        .filterNot { (k, _) ->
                            // Content-bearing keys blow the line up and never say
                            // what the call WAS.
                            k.lowercase() in setOf("content", "data", "body", "text", "source", "file", "image")
                        }
                        .joinToString("  ·  ") { (k, v) ->
                            val one = v.replace(Regex("""\s+"""), " ").trim()
                            "$k: " + if (one.length > 80) one.take(80) + "…" else one
                        }
                    if (args.isNotBlank()) {
                        BasicText(
                            AnnotatedString(args),
                            modifier = Modifier.padding(start = 22.dp, top = 2.dp),
                            style = HbType.readout.copy(
                                fontSize = 10.5.sp, lineHeight = 1.5.em, color = palette.textFaint,
                            ),
                            maxLines = 3,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }

                    val summary = tool.resultSummary()
                    if (summary != null && state != ToolStepState.Running) {
                        BasicText(
                            AnnotatedString("→ $summary"),
                            modifier = Modifier.padding(start = 22.dp, top = 2.dp),
                            style = HbType.read.copy(
                                fontSize = 11.5.sp,
                                color = if (state == ToolStepState.Failed) palette.red else palette.textDim,
                            ),
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }
    }
}

/** Elapsed, in the shortest form still precise enough to watch tick. Sub-minute
 *  is tenths — a counter that only moves once a second reads as frozen — and past
 *  a minute it becomes m:ss, where tenths are noise. */
private fun elapsed(ms: Long): String {
    if (ms < 60_000) return String.format("%.1fs", ms / 1000.0)
    val s = ms / 1000
    return "${s / 60}:${(s % 60).toString().padStart(2, '0')}"
}

@Composable
private fun BasicLabel(text: String, color: Color) {
    BasicText(
        AnnotatedString(text.uppercase()),
        style = HbType.label.copy(fontSize = 9.5.sp, fontWeight = FontWeight.Bold, color = color),
    )
}

/** The running marker — the same ring the transcript uses, so a step reads the
 *  same in both places. */
@Composable
private fun Spinner(color: Color) {
    val transition = rememberInfiniteTransition(label = "step")
    val sweep by transition.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(700), RepeatMode.Restart),
        label = "sweep",
    )
    Canvas(Modifier.size(13.dp)) {
        drawArc(
            color = color.copy(alpha = 0.25f),
            startAngle = 0f, sweepAngle = 360f, useCenter = false,
            style = Stroke(width = size.minDimension * 0.13f),
            topLeft = Offset.Zero, size = size,
        )
        drawArc(
            color = color,
            startAngle = sweep, sweepAngle = 90f, useCenter = false,
            style = Stroke(width = size.minDimension * 0.13f),
            topLeft = Offset.Zero, size = size,
        )
    }
}

/** Done and failed share a shape budget: one 13dp glyph, drawn rather than
 *  iconised, so the column stays one width whatever the outcome. */
@Composable
private fun Mark(color: Color) {
    Canvas(Modifier.size(13.dp)) {
        val w = size.minDimension
        drawCircle(color = color.copy(alpha = 0.18f), radius = w / 2f)
        drawCircle(color = color, radius = w * 0.17f)
    }
}
