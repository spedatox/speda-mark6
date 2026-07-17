package com.speda.heartbreaker.ui.chat

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.data.Downloader
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.ChatMessage
import com.speda.heartbreaker.domain.MarkdownPrep
import com.speda.heartbreaker.domain.Role
import com.speda.heartbreaker.domain.Segment
import com.speda.heartbreaker.domain.ToolStatus
import com.speda.heartbreaker.domain.Typewriter
import com.speda.heartbreaker.domain.buildSegments
import com.speda.heartbreaker.ui.HbText
import com.speda.heartbreaker.ui.prose.ProseText
import kotlinx.coroutines.delay
import kotlin.math.floor

/** The reveal target — content length; extracted so the throttle reads it cheaply. */
private fun targetLenOf(content: String): Int = content.length

/**
 * One chat row. Assistant rows interleave text + tool activity at the char offset
 * each tool fired (buildSegments), reveal text with the adaptive typewriter, and
 * keep streamed content on screen even when a mid-turn error banner appears —
 * a faithful port of Message.tsx (M1 renders text as plain prose; the full
 * markdown/prose renderer lands in M2).
 */
@Composable
fun MessageItem(
    message: ChatMessage,
    config: AppConfig? = null,
    downloader: Downloader? = null,
) {
    if (message.role == Role.User) UserRow(message) else AssistantRow(message, config, downloader)
}

@Composable
private fun UserRow(message: ChatMessage) {
    val palette = LocalHbPalette.current
    Row(Modifier.fillMaxWidth().padding(bottom = 12.dp), horizontalArrangement = Arrangement.End) {
        if (message.content.isNotEmpty()) {
            Box(
                Modifier
                    .wrapContentWidth()
                    .widthIn(max = 320.dp)
                    .hbGlass(shape = HbGlassShape.R12, state = HbGlassState.Tint(palette.accent))
                    .padding(horizontal = 14.dp, vertical = 10.dp),
            ) {
                HbText(message.content, style = HbType.read, color = palette.text)
            }
        }
    }
}

@Composable
private fun AssistantRow(message: ChatMessage, config: AppConfig?, downloader: Downloader?) {
    val palette = LocalHbPalette.current
    val hasCodeBlock = message.content.contains("```")

    // ── Typewriter reveal (withFrameNanos, adaptive catch-up) ────────────────
    var revealed by remember { mutableFloatStateOf(if (message.isStreaming && !hasCodeBlock) 0f else message.content.length.toFloat()) }
    var displayLen by remember { mutableIntStateOf(revealed.toInt()) }

    LaunchedEffect(message.content, hasCodeBlock) {
        if (hasCodeBlock) { revealed = message.content.length.toFloat(); displayLen = message.content.length; return@LaunchedEffect }
        var last = 0L
        while (revealed < message.content.length) {
            withFrameNanos { now ->
                val dt = if (last == 0L) 0.0 else (now - last) / 1_000_000_000.0
                last = now
                revealed = Typewriter.advance(revealed.toDouble(), message.content.length, dt).toFloat()
                displayLen = floor(revealed).toInt()
            }
        }
        displayLen = message.content.length
    }

    val fullLen = message.content.length
    val isRevealing = displayLen < fullLen

    // Throttle the markdown parse during streaming — the typewriter runs at 60fps
    // but the prose only needs ~12fps to look smooth. This is the #1 streaming
    // cost in the web too (Message.tsx debounces it at 80ms).
    var renderLen by remember { mutableIntStateOf(displayLen) }
    LaunchedEffect(isRevealing) {
        if (!isRevealing) {
            renderLen = targetLenOf(message.content)
            return@LaunchedEffect
        }
        while (true) {
            delay(80)
            renderLen = floor(revealed).toInt()
        }
    }
    val revealedLen = if (isRevealing) renderLen.coerceIn(0, fullLen) else fullLen

    val segments = remember(message.content, message.tools, revealedLen) {
        buildSegments(message.content, message.tools, revealedLen)
    }

    Row(Modifier.fillMaxWidth().padding(bottom = 18.dp)) {
        Column(Modifier.fillMaxWidth()) {
            if (message.content.isNotEmpty() || message.tools.isNotEmpty()) {
                segments.forEachIndexed { i, seg ->
                    val isLast = i == segments.lastIndex
                    when (seg) {
                        is Segment.Tools -> ToolFeed(seg.tools, streaming = message.isStreaming)
                        is Segment.Text -> {
                            // Each settled segment's prepared text is remembered by
                            // value, so only the live tail re-parses as the
                            // typewriter advances (the web's per-segment memo).
                            val prepared = remember(seg.text) { MarkdownPrep.prepare(seg.text) }
                            ProseText(prepared)
                            if (isLast && (message.isStreaming || (!hasCodeBlock && isRevealing))) {
                                StreamingCursor()
                            }
                        }
                    }
                }
            } else if (message.isStreaming) {
                WorkingStatus(lastToolName = message.tools.lastOrNull()?.name, status = message.status)
            }

            // Downloadable files the agent produced this turn.
            val files = message.files
            if (!files.isNullOrEmpty() && config != null && downloader != null) {
                files.forEach { f -> FileCard(file = f, config = config, downloader = downloader) }
            }

            if (message.isError) {
                val top = if (message.content.isNotEmpty() || message.tools.isNotEmpty()) 8.dp else 0.dp
                Box(
                    Modifier
                        .padding(top = top)
                        .clip(RoundedCornerShape(10.dp))
                        .background(Color(red = 248, green = 113, blue = 113).copy(alpha = 0.07f))
                        .padding(horizontal = 12.dp, vertical = 10.dp),
                ) {
                    HbText(
                        message.errorNote ?: message.content.ifEmpty { "Something went wrong." },
                        style = HbType.read,
                        color = Color(0xFFF87171),
                    )
                }
            }
        }
    }
}

/** Spinner + shimmering label — the "something's happening" indicator. */
@Composable
private fun WorkingStatus(lastToolName: String?, status: String?) {
    val palette = LocalHbPalette.current
    val label = if (lastToolName != null) ToolStatus.statusLabel(lastToolName) else (status ?: "Thinking")
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(9.dp),
        modifier = Modifier.padding(vertical = 2.dp),
    ) {
        Spinner()
        HbText("$label…", style = HbType.read.copy(textAlign = TextAlign.Start), color = palette.accentBright)
    }
}

/** Breathing streaming caret (CSS caretBreathe). */
@Composable
private fun StreamingCursor() {
    val palette = LocalHbPalette.current
    val transition = rememberInfiniteTransition(label = "caret")
    val alpha by transition.animateFloat(
        initialValue = 1f,
        targetValue = 0.4f,
        animationSpec = infiniteRepeatable(tween(1150), RepeatMode.Reverse),
        label = "caretBreathe",
    )
    Box(
        Modifier
            .padding(start = 3.dp)
            .size(width = 3.dp, height = 18.dp)
            .background(palette.accentBright.copy(alpha = alpha)),
    )
}
