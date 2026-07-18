package com.speda.heartbreaker.ui.chat

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.speech.tts.TextToSpeech
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
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
import java.util.Locale
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
    onDelete: (() -> Unit)? = null,
    onRegenerate: (() -> Unit)? = null,
    onEditAndResend: ((String) -> Unit)? = null,
) {
    if (message.role == Role.User) {
        UserRow(message, onDelete = onDelete, onEditAndResend = onEditAndResend)
    } else {
        AssistantRow(message, config, downloader, onDelete = onDelete, onRegenerate = onRegenerate)
    }
}

@Composable
private fun UserRow(
    message: ChatMessage,
    onDelete: (() -> Unit)?,
    onEditAndResend: ((String) -> Unit)?,
) {
    val palette = LocalHbPalette.current
    var showActions by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf(false) }
    var editValue by remember(message.id) { mutableStateOf(message.content) }

    // Inline edit-and-resend: the bubble becomes a glass textarea with
    // Cancel / Save & Send (Message.tsx user-edit branch).
    if (editing) {
        Column(Modifier.fillMaxWidth().padding(bottom = 12.dp), horizontalAlignment = Alignment.End) {
            EditBox(value = editValue, onValueChange = { editValue = it })
            Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                PillButton("Cancel", tint = null) { editing = false; editValue = message.content; showActions = false }
                PillButton("Save & Send", tint = palette.accentBright) {
                    val v = editValue.trim()
                    editing = false; showActions = false
                    if (v.isNotEmpty() && v != message.content) onEditAndResend?.invoke(v)
                }
            }
        }
        return
    }

    Column(
        Modifier.fillMaxWidth().padding(bottom = 12.dp),
        horizontalAlignment = Alignment.End,
    ) {
        // Attached images — thumbnails, tap to open full screen.
        val images = message.images
        if (!images.isNullOrEmpty()) {
            Row(
                Modifier.padding(bottom = 6.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                images.forEach { src ->
                    val bmp = rememberDataUrlImage(src)
                    if (bmp != null) {
                        Image(
                            bitmap = bmp,
                            contentDescription = null,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier
                                .size(150.dp)
                                .clip(RoundedCornerShape(10.dp)),
                        )
                    }
                }
            }
        }

        // Non-image uploads — display chips, not downloadable (just a marker).
        val uploads = message.uploads
        if (!uploads.isNullOrEmpty()) {
            Row(
                Modifier.padding(bottom = 6.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                uploads.forEach { u -> UploadChip(u.name, u.size) }
            }
        }

        if (message.content.isNotEmpty()) {
            Box(
                Modifier
                    .wrapContentWidth()
                    .widthIn(max = 320.dp)
                    .hbGlass(shape = HbGlassShape.R12, state = HbGlassState.Tint(palette.accent))
                    .noRippleClick { showActions = !showActions }
                    .padding(horizontal = 14.dp, vertical = 10.dp),
            ) {
                HbText(message.content, style = HbType.read, color = palette.text)
            }
        }

        // No hover on touch — tap the bubble to reveal the action bar
        // (the plan's (pointer: coarse) behaviour).
        if (showActions && message.content.isNotEmpty()) {
            ActionBar {
                if (onEditAndResend != null) {
                    ActionBtn({ HbGlyphs.Edit(it) }) { editValue = message.content; editing = true }
                }
                CopyBtn(message.content)
                if (onDelete != null) ActionBtn({ HbGlyphs.Trash(it) }) { onDelete() }
            }
        }
    }
}

/** A file the owner attached — a marker chip, not a download (Message.tsx). */
@Composable
private fun UploadChip(name: String, size: Long) {
    val palette = LocalHbPalette.current
    Column(
        Modifier
            .widthIn(max = 240.dp)
            .clip(RoundedCornerShape(9.dp))
            .background(palette.accent.copy(alpha = 0.08f))
            .border(1.dp, palette.accent.copy(alpha = 0.28f), RoundedCornerShape(9.dp))
            .padding(horizontal = 10.dp, vertical = 7.dp),
    ) {
        HbText(name, style = HbType.read.copy(fontSize = 12.sp), color = palette.text, maxLines = 1)
        if (size > 0) {
            HbText(
                fmtBytes(size),
                style = HbType.readout.copy(fontSize = 9.5.sp),
                color = palette.iconDim,
            )
        }
    }
}

@Composable
private fun AssistantRow(
    message: ChatMessage,
    config: AppConfig?,
    downloader: Downloader?,
    onDelete: (() -> Unit)?,
    onRegenerate: (() -> Unit)?,
) {
    val palette = LocalHbPalette.current
    val hasCodeBlock = message.content.contains("```")
    var showActions by remember { mutableStateOf(false) }

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
        Column(Modifier.fillMaxWidth().noRippleClick { if (!message.isStreaming) showActions = !showActions }) {
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

            // Action bar — tap the answer to reveal it once streaming ends
            // (Message.tsx assistant action bar: copy, thumbs, read-aloud,
            // regenerate, delete).
            if (showActions && !message.isStreaming && message.content.isNotEmpty()) {
                ActionBar(Modifier.padding(top = 6.dp)) {
                    CopyBtn(message.content)
                    ThumbsBtns()
                    ReadAloudBtn(message.content)
                    if (onRegenerate != null) ActionBtn({ HbGlyphs.Refresh(it) }) { showActions = false; onRegenerate() }
                    if (onDelete != null) ActionBtn({ HbGlyphs.Trash(it) }) { onDelete() }
                }
            }
        }
    }
}

// ── Action bar plumbing ─────────────────────────────────────────────────────

/** A tap without the Material ripple (foundation-only, no theme pulled in). */
private fun Modifier.noRippleClick(onClick: () -> Unit): Modifier = this.composed {
    clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClick)
}

/** The hover-less action row (opacity fade → simple presence on tap). */
@Composable
private fun ActionBar(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Row(
        modifier,
        horizontalArrangement = Arrangement.spacedBy(2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) { content() }
}

/** 34dp square glyph button (44dp web target → touch-comfortable on phone). */
@Composable
private fun ActionBtn(icon: @Composable (Color) -> Unit, tint: Color? = null, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Box(
        Modifier
            .size(34.dp)
            .clip(RoundedCornerShape(7.dp))
            .noRippleClick(onClick),
        contentAlignment = Alignment.Center,
    ) {
        icon(tint ?: palette.iconDim)
    }
}

/** Copy with the check-confirm swap (2s), writing to the system clipboard. */
@Composable
private fun CopyBtn(text: String) {
    val palette = LocalHbPalette.current
    val clipboard = LocalClipboardManager.current
    var copied by remember { mutableStateOf(false) }
    LaunchedEffect(copied) { if (copied) { delay(2000); copied = false } }
    ActionBtn(
        icon = { c -> if (copied) HbGlyphs.Check(c) else HbGlyphs.Copy(c) },
        tint = if (copied) palette.accent else null,
    ) {
        clipboard.setText(AnnotatedString(text))
        copied = true
    }
}

/** Good / bad response — local latch, mutually exclusive (Message.tsx). */
@Composable
private fun ThumbsBtns() {
    val palette = LocalHbPalette.current
    var up by remember { mutableStateOf(false) }
    var down by remember { mutableStateOf(false) }
    ActionBtn({ HbGlyphs.ThumbUp(it) }, tint = if (up) palette.accent else null) { up = !up; down = false }
    ActionBtn({ HbGlyphs.ThumbDown(it) }, tint = if (down) palette.red else null) { down = !down; up = false }
}

/** Read aloud via Android TTS — strips markdown syntax like the web does. */
@Composable
private fun ReadAloudBtn(text: String) {
    val palette = LocalHbPalette.current
    val context = LocalContext.current
    var speaking by remember { mutableStateOf(false) }
    val tts = remember {
        var engine: TextToSpeech? = null
        engine = TextToSpeech(context) { status ->
            if (status == TextToSpeech.SUCCESS) engine?.language = Locale.US
        }
        engine
    }
    DisposableEffect(Unit) {
        onDispose { tts?.stop(); tts?.shutdown() }
    }
    ActionBtn({ HbGlyphs.Speaker(it) }, tint = if (speaking) palette.accent else null) {
        if (speaking) {
            tts?.stop(); speaking = false
        } else {
            val clean = text.replace(Regex("[#*`>]"), "").trim()
            tts?.speak(clean, TextToSpeech.QUEUE_FLUSH, null, "hb-read")
            speaking = true
        }
    }
}

/** Glass text button used by the inline user-edit Cancel / Save & Send. */
@Composable
private fun PillButton(label: String, tint: Color?, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    val c = tint ?: palette.iconBright
    Box(
        Modifier
            .hbGlass(shape = HbGlassShape.R9, state = if (tint != null) HbGlassState.Tint(tint) else HbGlassState.Default)
            .noRippleClick(onClick)
            .padding(horizontal = 14.dp, vertical = 8.dp),
        contentAlignment = Alignment.Center,
    ) {
        HbText(label, style = HbType.headerBar.copy(fontSize = 12.sp), color = c, caps = true)
    }
}

/** Glass multiline editor for the inline edit-and-resend flow. */
@Composable
private fun EditBox(value: String, onValueChange: (String) -> Unit) {
    val palette = LocalHbPalette.current
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        textStyle = HbType.read.merge(TextStyle(color = palette.text)),
        cursorBrush = SolidColor(palette.accentBright),
        modifier = Modifier.fillMaxWidth(),
        decorationBox = { inner ->
            Box(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = 72.dp)
                    .hbGlass(shape = HbGlassShape.R12, state = HbGlassState.Active)
                    .padding(horizontal = 12.dp, vertical = 10.dp),
            ) { inner() }
        },
    )
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
