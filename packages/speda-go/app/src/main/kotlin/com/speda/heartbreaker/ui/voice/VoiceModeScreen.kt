// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.voice

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.VoiceSpeaker
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.ToolBadge
import com.speda.heartbreaker.domain.captionOf
import com.speda.heartbreaker.domain.splitBoardPanels
import com.speda.heartbreaker.ui.prose.FenceBlock
import kotlinx.coroutines.delay

/**
 * Voice mode — the phone's presentation surface.
 *
 * The agent narrates; this carries the evidence. Windows appear in the order it
 * staged them, the words run underneath as a live caption, and the orb steps
 * aside once there is something to show.
 *
 * ── Why this is not the desktop's board ─────────────────────────────────────
 * There, windows float over a void and the owner drags and resizes them. Those
 * are mouse gestures. Here the board is a COLUMN, in staged order, full width:
 * the same content, the same order, laid out the way a phone is read. The orb
 * still shrinks into the corner when the column fills — that gesture means
 * something ("it is presenting rather than talking") and survives the change of
 * layout intact.
 *
 * ── The caption is a subtitle, not a transcript ─────────────────────────────
 * It shows what is being said NOW, a few lines deep, and scrolls. It is not
 * scrollback: anything worth reading twice was supposed to become a window, and
 * a full transcript here would make this the chat screen with a bigger font.
 */
/**
 * Whether a turn has been silent long enough to deserve the activity card.
 *
 * A threshold rather than "always", because a one-word answer is supposed to
 * leave the orb alone — opening a board for "what time is it" would undo the
 * one behaviour the mode is careful about. A turn sitting silent past this,
 * though, is indistinguishable from a hang.
 */
@Composable
private fun slowSince(streaming: Boolean, silent: Boolean, afterMs: Int): Boolean {
    var slow by remember { mutableStateOf(false) }
    LaunchedEffect(streaming, silent, afterMs) {
        if (!streaming || !silent) { slow = false; return@LaunchedEffect }
        delay(afterMs.toLong())
        slow = true
    }
    return slow
}

@Composable
fun VoiceModeScreen(
    /** The live reply, exactly as it is arriving. */
    reply: String,
    /** The turn's tool calls, as they fire. This is the surface where they matter
     *  MOST: there is no answer on screen to reassure the owner while a research
     *  turn spends two minutes browsing. */
    tools: List<ToolBadge>,
    /** True while the reply is still arriving. */
    streaming: Boolean,
    state: VoiceSpeaker.State,
    level: Float,
    /** How many caption lines stay on screen — from Settings → Canvas, so the
     *  phone and the desktop are tuned in one place. */
    captionLines: Int,
    maxPanels: Int,
    /** How long a silent turn may run before the activity card appears. */
    activityAfterMs: Int,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current

    // The staged windows and the narration, cut from the same text. Recomputed as
    // it streams, which is what makes a window appear at the point in the
    // narration the agent placed it — writing order is the cue track.
    val panels = remember(reply, maxPanels) { splitBoardPanels(reply).take(maxPanels) }
    val caption = remember(reply) { captionOf(reply) }

    /* The activity card leads the column, because while a turn is working it is
     * the only thing on it worth looking at — and because "nothing is happening"
     * was the whole complaint this answers.
     *
     * WHEN it shows is the delicate part. Always would dock the orb for "what
     * time is it", which is exactly the case the mode should leave alone. So it
     * appears on evidence of WORK: a tool has fired, or the turn has been silent
     * past the owner's patience setting. */
    // Called unconditionally: a composable behind && is a conditional call, and
    // its remembered state would be thrown away every time the condition flips.
    val slow = slowSince(streaming, caption.isBlank(), activityAfterMs)
    val showActivity = tools.isNotEmpty() || (streaming && slow)
    val docked = panels.isNotEmpty() || showActivity

    val boardScroll = rememberScrollState()
    val captionScroll = rememberScrollState()

    // Both columns ride their own tail: the newest window and the words being
    // spoken right now are the parts worth having on screen.
    LaunchedEffect(panels.size) { boardScroll.animateScrollTo(boardScroll.maxValue) }
    LaunchedEffect(caption) { captionScroll.animateScrollTo(captionScroll.maxValue) }

    Box(modifier.fillMaxSize().background(palette.void)) {

        // ── The board ───────────────────────────────────────────────────────
        Column(
            Modifier
                .fillMaxSize()
                .padding(horizontal = 12.dp)
                .padding(top = 8.dp, bottom = 132.dp)
                .verticalScroll(boardScroll),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            // Rendered through the SAME dispatcher chat uses, so every kind
            // works here — a chart, a map, a stat tile, a dossier card — and none
            // of them is a second implementation that can drift from its
            // counterpart in the transcript.
            if (showActivity) {
                VoiceActivityCard(
                    tools = tools,
                    streaming = streaming,
                    hasText = caption.isNotBlank(),
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            panels.forEach { p -> FenceBlock(language = p.info, code = p.body) }
        }

        // ── The orb ─────────────────────────────────────────────────────────
        // Centred while it is the only thing here; shrunk into the bottom-right
        // once the board has something on it. One animated scale rather than two
        // orbs, so it MOVES aside instead of being replaced by a smaller one.
        val scale = orbScale(docked)
        Box(
            Modifier
                .align(if (docked) Alignment.BottomEnd else Alignment.Center)
                .padding(if (docked) 4.dp else 0.dp)
                .fillMaxWidth(0.82f)
                .aspectRatio(1f)
                .scale(scale),
            contentAlignment = Alignment.Center,
        ) {
            VoiceOrb(state = state, level = level, modifier = Modifier.fillMaxSize())
        }

        // ── The caption ─────────────────────────────────────────────────────
        if (caption.isNotBlank()) {
            Box(
                Modifier
                    .align(Alignment.BottomStart)
                    .fillMaxWidth(if (docked) 0.62f else 1f)
                    .padding(start = 16.dp, end = 12.dp, bottom = 18.dp),
            ) {
                val fontSize = if (docked) 14.sp else 17.sp
                val lineHeight = fontSize * 1.5f
                // Converted through the density rather than assumed: sp and dp
                // diverge the moment the owner scales their font, and a cap in
                // dp against text in sp clips the last line for exactly the
                // people who most need it not to.
                val capHeight = with(LocalDensity.current) {
                    (lineHeight.toPx() * captionLines).toDp()
                }
                BasicText(
                    AnnotatedString(caption),
                    modifier = Modifier
                        .heightIn(max = capHeight)
                        .verticalScroll(captionScroll),
                    style = HbType.read.copy(
                        fontSize = fontSize,
                        lineHeight = lineHeight,
                        color = palette.text,
                    ),
                )
            }
        }
    }
}
