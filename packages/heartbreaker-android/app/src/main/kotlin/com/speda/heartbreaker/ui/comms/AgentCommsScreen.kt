package com.speda.heartbreaker.ui.comms

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
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
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbText
import com.speda.heartbreaker.ui.prose.ProseText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * AGENT_COMMS — the inter-agent traffic tray. Mobile port of CommsTray.tsx +
 * CommBubble.tsx: a full-width bottom slab (the mobile spec calls for a
 * "full-width Comms Tray", not the desktop's 420px floating panel) showing
 * live dispatch traffic between SPEDA and the roster (GET /agents/comms,
 * written by app/core/dispatch.py) as a chat scrollback. EXTEND_ grows it
 * toward a near-full-height traffic console; also hosts the House Party
 * Protocol stand-down control (engaging is voice-only, never from this UI).
 */

private const val POLL_MS = 3000L

@Composable
fun AgentCommsScreen(config: AppConfig, api: IgorApi, onClose: () -> Unit) {
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()

    var entries by remember { mutableStateOf<List<AgentCommEntry>>(emptyList()) }
    var loaded by remember { mutableStateOf(false) }
    var wide by remember { mutableStateOf(false) }
    var party by remember { mutableStateOf(false) }

    LaunchedEffect(config) {
        party = api.getHouseParty(config)
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
                    .hbGlass(shape = HbGlassShape.TopOnly, state = HbGlassState.Menu)
                    .navigationBarsPadding(),
            ) {
                // ── Header ───────────────────────────────────────────────────
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    HbText("AGENT_COMMS // TRAFFIC", style = HbType.headerBar.copy(fontSize = 11.sp), color = palette.text, caps = true)
                    if (live > 0) {
                        HbText("$live LIVE", style = HbType.readout.copy(fontSize = 9.sp), color = palette.amber)
                    }
                    Spacer(Modifier.weight(1f))
                    Row(
                        Modifier
                            .hbGlass(shape = HbGlassShape.Pill, state = if (party) HbGlassState.Tint(palette.amber) else HbGlassState.Default)
                            .clickable(enabled = party) {
                                scope.launch { party = false; party = api.setHouseParty(config, false) }
                            }
                            .padding(horizontal = 8.dp, vertical = 5.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(5.dp),
                    ) {
                        Box(
                            Modifier.size(5.dp).background(
                                if (party) palette.amber else palette.iconDim,
                                CircleShape,
                            ),
                        )
                        HbText(
                            if (party) "HPP · STAND DOWN" else "HPP OFFLINE",
                            style = HbType.headerBar.copy(fontSize = 9.sp),
                            color = if (party) palette.amberBright else palette.textFaint,
                        )
                    }
                    Row(
                        Modifier.clickable { wide = !wide },
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(3.dp),
                    ) {
                        HbText(
                            if (wide) "RETRACT_" else "EXTEND_",
                            style = HbType.readout.copy(fontSize = 9.sp),
                            color = if (wide) palette.amber else palette.icon,
                        )
                        if (wide) HbGlyphs.ChevronDown(palette.amber, size = 9.dp) else HbGlyphs.ChevronUp(palette.icon, size = 9.dp)
                    }
                    Box(Modifier.clickable(onClick = onClose)) {
                        HbGlyphs.Close(palette.iconDim, size = 12.dp)
                    }
                }

                // ── Feed ─────────────────────────────────────────────────────
                if (entries.isEmpty()) {
                    HbText(
                        if (loaded) "// NO TRAFFIC — DISPATCHES BETWEEN AGENTS WILL APPEAR HERE" else "// LINKING…",
                        style = HbType.readout.copy(fontSize = 10.sp),
                        color = palette.textFaint,
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                    )
                } else {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxWidth().weight(1f, fill = false),
                        contentPadding = PaddingValues(horizontal = 10.dp, vertical = 6.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        items(entries, key = { it.id }) { e -> CommBubble(e, compact = !wide) }
                    }
                }
            }
        }
    }
}

@Composable
private fun CommBubble(e: AgentCommEntry, compact: Boolean) {
    val palette = LocalHbPalette.current
    val clipboard = LocalClipboardManager.current
    var open by remember { mutableStateOf(false) }

    val from = hexColor(Brands.agentColor(e.fromAgent))
    val to = hexColor(Brands.agentColor(e.toAgent))
    val failed = e.status in setOf("error", "timeout", "offline")
    val clip = if (compact) 200 else 420
    val result = e.result.orEmpty()
    val clipped = e.task.length > clip || result.length > clip
    val showTask = if (open || e.task.length <= clip) e.task else e.task.take(clip) + "…"
    val showResult = if (open || result.length <= clip) result else result.take(clip) + "…"

    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Avatar(id = e.fromAgent, color = from, size = if (compact) 22.dp else 26.dp)
        Column(
            Modifier
                .weight(1f)
                .hbGlass(shape = HbGlassShape.R12, state = HbGlassState.Tint(from))
                .padding(horizontal = 10.dp, vertical = 8.dp),
        ) {
            // meta line: SPEDA ▸ SENTINEL · 06:13:42 · HP · copy/expand controls
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                HbText(e.fromAgent.uppercase(Locale.ENGLISH), style = HbType.readout.copy(fontSize = 9.sp, fontWeight = FontWeight.Bold), color = from)
                HbText("▸", style = HbType.readout.copy(fontSize = 9.sp), color = palette.iconDim)
                HbText(e.toAgent.uppercase(Locale.ENGLISH), style = HbType.readout.copy(fontSize = 9.sp, fontWeight = FontWeight.Bold), color = to)
                HbText(fmtCommTime(e.createdAt), style = HbType.readout.copy(fontSize = 9.sp), color = palette.textFaint)
                if (e.protocol == "house_party") HbText("HP", style = HbType.readout.copy(fontSize = 9.sp), color = palette.amber)
                if (e.kind == "broadcast") HbText("BROADCAST", style = HbType.readout.copy(fontSize = 9.sp), color = palette.amber)
                Spacer(Modifier.weight(1f))
                Box(
                    Modifier.clickable {
                        val full = if (result.isNotEmpty()) "${e.task}\n\n--- ${e.toAgent.uppercase(Locale.ENGLISH)} ---\n$result" else e.task
                        clipboard.setText(AnnotatedString(full))
                    },
                ) { HbGlyphs.Copy(palette.iconDim, size = 11.dp) }
                if (clipped) {
                    HbText(
                        if (open) "LESS_" else "MORE_",
                        style = HbType.readout.copy(fontSize = 8.5.sp),
                        color = if (open) palette.amber else palette.icon,
                        modifier = Modifier.clickable { open = !open },
                    )
                }
            }

            Spacer(Modifier.height(4.dp))
            ProseText(showTask, modifier = Modifier.fillMaxWidth())

            // the reply, nested — the target agent answering in the thread
            if (e.status == "running") {
                HbText(
                    "${e.toAgent.uppercase(Locale.ENGLISH)} WORKING… " + elapsedLabel(e.createdAt),
                    style = HbType.readout.copy(fontSize = 9.5.sp),
                    color = palette.amber,
                    modifier = Modifier.padding(top = 4.dp),
                )
            } else if (result.isNotEmpty()) {
                Column(
                    Modifier
                        .padding(top = 6.dp)
                        .background(if (failed) palette.red.copy(alpha = 0.06f) else Color.Transparent)
                        .padding(start = 8.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        HbText(e.toAgent.uppercase(Locale.ENGLISH), style = HbType.readout.copy(fontSize = 9.sp, fontWeight = FontWeight.Bold), color = to)
                        if (failed) HbText(e.status.uppercase(Locale.ENGLISH), style = HbType.readout.copy(fontSize = 9.sp), color = palette.red)
                        e.durationMs?.let { HbText("${it / 1000.0}s", style = HbType.readout.copy(fontSize = 9.sp), color = palette.textFaint) }
                    }
                    if (failed) {
                        HbText(showResult, style = HbType.read.copy(fontSize = 13.sp), color = Color(0xFFD98A7A))
                    } else {
                        ProseText(showResult, modifier = Modifier.fillMaxWidth())
                    }
                }
            }
        }
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

private fun parseIsoUtc(iso: String): Instant {
    val withZone = if (iso.endsWith("Z") || iso.contains("+")) iso else "${iso}Z"
    return runCatching { Instant.parse(withZone) }.getOrElse { Instant.now() }
}

private fun fmtCommTime(iso: String): String =
    CLOCK.format(parseIsoUtc(iso).atZone(ZoneId.systemDefault()))

private fun hexColor(hex: String): Color =
    runCatching { Color(android.graphics.Color.parseColor(hex)) }.getOrDefault(Color(0xFF5D7F8A))
