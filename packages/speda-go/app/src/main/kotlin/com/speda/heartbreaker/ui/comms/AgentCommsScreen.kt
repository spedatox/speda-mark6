package com.speda.heartbreaker.ui.comms

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.activity.compose.BackHandler
import com.speda.heartbreaker.data.AgentCommEntry
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.designsystem.brand.AgentMark
import com.speda.heartbreaker.designsystem.brand.AgentMarks
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.brand.Finish
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.glass.toShape
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.CommTranscript
import com.speda.heartbreaker.ui.HbText
import com.speda.heartbreaker.ui.prose.ProseText
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * AGENT_COMMS — the inter-agent traffic tray. Mobile port of CommsTray.tsx +
 * CommBubble.tsx: a full-width bottom slab (the mobile spec calls for a
 * "full-width Comms Tray", not the desktop's 420px floating panel) showing
 * live dispatch traffic between Speda and the roster (GET /agents/comms,
 * written by app/core/dispatch.py) as a GROUP CHAT — every agent on one
 * timeline, each speaking for itself. Expand grows it toward a near-full-height
 * traffic console.
 *
 * No House Party control lives here any more: the protocol is a desktop
 * surface. The owner can still stand it down from this app by telling Speda.
 */

private const val POLL_MS = 3000L

@Composable
fun AgentCommsScreen(config: AppConfig, api: IgorApi, onClose: () -> Unit) {
    val palette = LocalHbPalette.current

    var entries by remember { mutableStateOf<List<AgentCommEntry>>(emptyList()) }
    var loaded by remember { mutableStateOf(false) }
    var wide by remember { mutableStateOf(false) }

    LaunchedEffect(config) {
        while (true) {
            // oldest first — a chat scrollback, newest at the bottom
            entries = api.fetchAgentComms(config, 120).asReversed()
            loaded = true
            delay(POLL_MS)
        }
    }

    // Predictive back retracts EXTEND_ first, then closes — the Esc semantics.
    BackHandler { if (wide) wide = false else onClose() }

    val listState = rememberLazyListState()
    val pinnedToEnd by remember {
        derivedStateOf {
            val info = listState.layoutInfo
            val last = info.visibleItemsInfo.lastOrNull()
            last == null || (last.index == info.totalItemsCount - 1 &&
                last.offset + last.size <= info.viewportEndOffset + 80)
        }
    }
    LaunchedEffect(entries.size, wide) {
        if (entries.isNotEmpty() && pinnedToEnd) listState.animateScrollToItem(entries.lastIndex)
    }

    val live = entries.count { it.status == "running" }

    Box(Modifier.fillMaxSize()) {
        // Scrim — tap outside the slab to close, like Esc on desktop.
        Box(
            Modifier.fillMaxSize()
                .background(Color.Black.copy(alpha = 0.35f))
                .clickable(onClick = onClose),
        )

        BoxWithConstraints(Modifier.align(Alignment.BottomCenter).fillMaxWidth()) {
            val maxH = maxHeight
            Column(
                Modifier
                    .fillMaxWidth()
                    .heightIn(max = if (wide) maxH * 0.82f else 300.dp)
                    .animateContentSize()
                    .hbGlass(shape = HbGlassShape.CardTop, state = HbGlassState.Menu)
                    .navigationBarsPadding(),
            ) {
                // ── Header ───────────────────────────────────────────────────
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Column(Modifier.weight(1f)) {
                        HbText("Agent traffic", style = HbType.headerBar.copy(fontSize = 15.sp), color = palette.text)
                        HbText(
                            if (live > 0) "$live working" else "${entries.size} messages",
                            style = HbType.read.copy(fontSize = 12.5.sp),
                            color = if (live > 0) palette.green else palette.textFaint,
                        )
                    }
                    Row(
                        Modifier.clickable { wide = !wide },
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(3.dp),
                    ) {
                        HbText(
                            if (wide) "Retract" else "Expand",
                            style = HbType.read.copy(fontSize = 13.sp),
                            color = if (wide) palette.accentBright else palette.accent,
                        )
                        if (wide) HbGlyphs.ChevronDown(palette.accentBright, size = 10.dp) else HbGlyphs.ChevronUp(palette.accent, size = 10.dp)
                    }
                    Box(Modifier.clickable(onClick = onClose)) {
                        HbGlyphs.Close(palette.iconDim, size = 12.dp)
                    }
                }

                // ── Feed ─────────────────────────────────────────────────────
                if (entries.isEmpty()) {
                    HbText(
                        if (loaded) "No traffic yet — dispatches between agents appear here." else "Linking…",
                        style = HbType.read.copy(fontSize = 14.sp),
                        color = palette.textFaint,
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                    )
                } else {
                    // One timeline, every agent speaking for itself — the same
                    // transcript the war room and the desktop tray render.
                    val rows = remember(entries) { CommTranscript.rows(entries) }
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxWidth().weight(1f, fill = false),
                        contentPadding = PaddingValues(horizontal = 10.dp, vertical = 6.dp),
                        verticalArrangement = Arrangement.spacedBy(2.dp),
                    ) {
                        items(rows, key = { it.msg.key }) { row ->
                            if (row.chip) TimeChip(row.msg.at)
                            CommLine(row.msg, head = row.head, compact = !wide)
                        }
                    }
                }
            }
        }
    }
}

/** The clock that marks a pause in the room. */
@Composable
private fun TimeChip(at: Long) {
    val palette = LocalHbPalette.current
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), horizontalArrangement = Arrangement.Center) {
        Box(
            Modifier
                .clip(CircleShape)
                .background(Color.White.copy(alpha = 0.03f))
                .padding(horizontal = 12.dp, vertical = 4.dp),
        ) {
            HbText(
                CLOCK_SHORT.format(Instant.ofEpochMilli(at).atZone(ZoneId.systemDefault())),
                style = HbType.readout.copy(fontSize = 11.sp),
                color = palette.textFaint,
            )
        }
    }
}

/**
 * One participant's line: mark, name, bubble.
 *
 * `head = false` continues the same agent's run — the mark and the name drop
 * away and the tail squares off, so a burst from one agent reads as one turn of
 * speech rather than three separate announcements.
 */
@Composable
private fun CommLine(m: CommTranscript.Msg, head: Boolean, compact: Boolean) {
    val palette = LocalHbPalette.current
    val clipboard = LocalClipboardManager.current
    var open by remember { mutableStateOf(false) }

    val c = hexColor(Brands.agentColor(m.agent))
    val tile = if (compact) 28.dp else 34.dp
    val clip = if (compact) 260 else 520
    val clipped = m.text.length > clip
    val body = if (open || !clipped) m.text else m.text.take(clip) + "…"

    // The tail marks who started speaking; a continuation squares off.
    val bubbleShape = if (head) {
        RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomEnd = 16.dp, bottomStart = 5.dp)
    } else {
        RoundedCornerShape(16.dp)
    }
    // An order going out carries the speaker's colour; work coming back is neutral.
    val bg = if (m.outbound) c.copy(alpha = 0.09f) else Color.White.copy(alpha = 0.03f)
    val rim = if (m.outbound) c.copy(alpha = 0.22f) else Color.White.copy(alpha = 0.06f)

    Row(
        Modifier.fillMaxWidth().padding(top = if (head) 8.dp else 2.dp),
        horizontalArrangement = Arrangement.spacedBy(9.dp),
    ) {
        if (head) {
            Box(
                Modifier
                    .size(tile)
                    .clip(HbGlassShape.Tile.toShape())
                    .background(c.copy(alpha = 0.16f))
                    .border(1.dp, c.copy(alpha = 0.34f), HbGlassShape.Tile.toShape()),
                contentAlignment = Alignment.Center,
            ) {
                Avatar(id = m.agent, color = c, size = tile * 0.61f)
            }
        } else {
            Spacer(Modifier.size(tile))
        }

        Column(Modifier.weight(1f)) {
            if (head) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    HbText(
                        m.agent.replaceFirstChar { it.uppercase() },
                        style = HbType.read.copy(fontSize = 13.sp, fontWeight = FontWeight.SemiBold),
                        color = c,
                    )
                    if (m.party) HbText("HP", style = HbType.readout.copy(fontSize = 10.sp), color = palette.amber)
                    if (m.broadcast) HbText("ALL HANDS", style = HbType.readout.copy(fontSize = 10.sp), color = palette.amber)
                }
                Spacer(Modifier.height(4.dp))
            }

            Column(
                Modifier
                    .clip(bubbleShape)
                    .background(bg)
                    .border(1.dp, rim, bubbleShape)
                    .padding(horizontal = 13.dp, vertical = 9.dp),
            ) {
                when {
                    m.running -> Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(9.dp),
                    ) {
                        WorkingRing(c)
                        HbText(
                            "working… " + elapsedLabel(m.since.orEmpty()),
                            style = HbType.read.copy(fontSize = 14.sp),
                            color = palette.textDim,
                        )
                    }
                    m.failed -> HbText(
                        if (m.text.isNotEmpty() && m.text != m.status) "${m.status}: $body" else m.status.orEmpty(),
                        style = HbType.read.copy(fontSize = 14.sp),
                        color = Color(0xFFE5897C),
                    )
                    else -> ProseText(body, modifier = Modifier.fillMaxWidth())
                }

                if (!m.running) {
                    Spacer(Modifier.height(4.dp))
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (clipped) {
                            HbText(
                                if (open) "Less" else "More",
                                style = HbType.readout.copy(fontSize = 11.sp),
                                color = if (open) palette.accentBright else palette.textFaint,
                                modifier = Modifier.clickable { open = !open },
                            )
                        }
                        Spacer(Modifier.weight(1f))
                        m.durationMs?.let {
                            HbText("${it / 1000.0}s", style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
                        }
                        HbText(
                            CLOCK.format(Instant.ofEpochMilli(m.at).atZone(ZoneId.systemDefault())),
                            style = HbType.readout.copy(fontSize = 11.sp),
                            color = palette.textFaint,
                        )
                        Box(Modifier.clickable { clipboard.setText(AnnotatedString(m.copyText)) }) {
                            HbGlyphs.Copy(palette.textFaint, size = 11.dp)
                        }
                    }
                }
            }
        }
    }
}

/** The ring every pending thing in the deck uses. */
@Composable
private fun WorkingRing(color: Color) {
    val spin = rememberInfiniteTransition(label = "working")
    val angle by spin.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(700, easing = LinearEasing)),
        label = "angle",
    )
    Canvas(Modifier.size(13.dp)) {
        val stroke = 1.5.dp.toPx()
        drawArc(
            color = color.copy(alpha = 0.30f),
            startAngle = 0f, sweepAngle = 360f, useCenter = false,
            style = Stroke(width = stroke),
        )
        drawArc(
            color = color,
            startAngle = angle, sweepAngle = 90f, useCenter = false,
            style = Stroke(width = stroke),
        )
    }
}

@Composable
private fun Avatar(id: String, color: Color, size: Dp) {
    // The agent's own mark, bare — no ring or plate around it. Below 28.dp the
    // glass bloom swallows the geometry, so small chips take the flat cut.
    // Only the initial fallback keeps a plate; a lone letter needs one to read.
    if (AgentMarks.has(id)) {
        AgentMark(
            agentId = id, color = color, size = size,
            finish = if (size >= 28.dp) Finish.Glass else Finish.Flat,
        )
        return
    }
    Box(
        Modifier.size(size).hbGlass(shape = HbGlassShape.Pill, state = HbGlassState.Tint(color)),
        contentAlignment = Alignment.Center,
    ) {
        HbText(id.take(1).uppercase(Locale.ENGLISH), style = HbType.headerBar.copy(fontSize = (size.value * 0.42).sp, fontWeight = FontWeight.ExtraBold), color = color)
    }
}

@Composable
private fun elapsedLabel(since: String): String {
    val start = remember(since) { parseIsoUtc(since).toEpochMilli() }
    var now by remember { mutableStateOf(System.currentTimeMillis()) }
    LaunchedEffect(since) { while (true) { delay(1000); now = System.currentTimeMillis() } }
    val s = maxOf(0L, (now - start) / 1000)
    return if (s < 60) "${s}s" else "${s / 60}m${s % 60}s"
}

private val CLOCK: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss", Locale.ENGLISH)

/** The pause marker reads to the minute — a room's quiet stretch is not a
 *  timestamp, it is "we picked this back up around then". */
private val CLOCK_SHORT: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm", Locale.ENGLISH)

private fun parseIsoUtc(iso: String): Instant {
    val withZone = if (iso.endsWith("Z") || iso.contains("+")) iso else "${iso}Z"
    return runCatching { Instant.parse(withZone) }.getOrElse { Instant.now() }
}

private fun hexColor(hex: String): Color =
    runCatching { Color(android.graphics.Color.parseColor(hex)) }.getOrDefault(Color(0xFF5D7F8A))
