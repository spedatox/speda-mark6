// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.AutomationAgent
import com.speda.heartbreaker.data.AutomationInfo
import com.speda.heartbreaker.data.AutomationRunInfo
import com.speda.heartbreaker.data.AutomationSaveResult
import com.speda.heartbreaker.data.AutomationsStatus
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private val KIND_LABEL = mapOf("web_watch" to "WEB", "rss_watch" to "RSS", "schedule" to "CRON", "webhook" to "HOOK")

/** Which face the tab shows — the watcher list, or the builder replacing it
 *  inline (never a nested modal — see AutomationBuilder.kt's doc). */
private sealed interface BuilderMode {
    data object Closed : BuilderMode
    data object New : BuilderMode
    data class Editing(val automation: AutomationInfo) : BuilderMode
    /** Viewing that automation's past firings instead of editing it — same
     *  "swap the tab body" convention, one more face on the same switch. */
    data class History(val automation: AutomationInfo) : BuilderMode
}

@Composable
fun AutomationsTab(config: AppConfig, graph: AppGraph) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val api = graph.api

    var autos by remember { mutableStateOf<List<AutomationInfo>>(emptyList()) }
    var agents by remember { mutableStateOf<List<AutomationAgent>>(emptyList()) }
    var status by remember { mutableStateOf<AutomationsStatus?>(null) }
    var tgMsg by remember { mutableStateOf("") }
    var mode by remember { mutableStateOf<BuilderMode>(BuilderMode.Closed) }
    // (automation id, message) — cleared automatically after a beat.
    var testMsg by remember { mutableStateOf<Pair<Int, String>?>(null) }
    var runs by remember { mutableStateOf<List<AutomationRunInfo>>(emptyList()) }
    var runsLoaded by remember { mutableStateOf(false) }
    var menuAutomation by remember { mutableStateOf<AutomationInfo?>(null) }

    suspend fun reload() {
        autos = api.getAutomations(config)
        status = api.getAutomationsStatus(config)
        agents = api.getAutomationAgents(config)
    }
    LaunchedEffect(config) { reload() }
    // Fetched live on each visit — no local cache, same as everything else
    // in this app except chat transcripts.
    LaunchedEffect(mode) {
        val m = mode
        if (m is BuilderMode.History) {
            runsLoaded = false
            runs = api.getAutomationRuns(config, m.automation.id)
            runsLoaded = true
        }
    }

    if (mode is BuilderMode.Closed) {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
        ) {
            SectionHeader(t.settingsAutomations.pipeline)
            Panel {
                val s = status
                StatusLine(
                    t.settingsAutomations.n8nEngine,
                    ok = s?.n8nOnline == true,
                    detail = when {
                        s == null -> t.settingsAutomations.checking
                        !s.n8nConfigured -> t.settingsAutomations.n8nNeedsKey
                        s.n8nOnline -> s.n8nUrl
                        else -> t.settingsAutomations.n8nUnreachable
                    },
                )
                Spacer(Modifier.height(6.dp))
                StatusLine(
                    t.settingsAutomations.telegramDelivery,
                    ok = s?.telegramConnected == true,
                    detail = when {
                        s == null -> t.settingsAutomations.checking
                        !s.telegramConfigured -> t.settingsAutomations.telegramNeedsToken
                        s.telegramConnected -> t.settingsAutomations.telegramConnected
                        else -> t.settingsAutomations.telegramReady
                    },
                )
                if (s?.telegramConfigured == true && !s.telegramConnected) {
                    Spacer(Modifier.height(10.dp))
                    SettingsButton(t.settingsAutomations.connectTelegram, onClick = {
                        scope.launch {
                            tgMsg = t.settingsAutomations.openingTelegram
                            val link = api.telegramConnect(config)
                            if (link == null) { tgMsg = t.settingsAutomations.couldntStartConnect; return@launch }
                            openUrl(context, link)
                            tgMsg = t.settingsAutomations.tapStart
                            repeat(40) {
                                delay(3000)
                                if (api.telegramConnected(config)) { tgMsg = ""; reload(); return@launch }
                            }
                            tgMsg = t.settingsAutomations.noResponseYet
                        }
                    })
                    if (tgMsg.isNotEmpty()) {
                        Spacer(Modifier.height(8.dp))
                        HbText(tgMsg, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textDim)
                    }
                }
            }

            SectionHeader(t.settingsAutomations.watchers)
            if (autos.isEmpty()) {
                Panel {
                    HbText(t.settingsAutomations.nothingWatched, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
                }
            } else {
                autos.forEach { a ->
                    AutomationCard(
                        a = a,
                        testLabel = testMsg?.takeIf { it.first == a.id }?.second,
                        onToggle = { active ->
                            autos = autos.map { if (it.id == a.id) it.copy(active = active) else it }
                            scope.launch { api.toggleAutomation(config, a.id, active); reload() }
                        },
                        onClick = { mode = BuilderMode.Editing(a) },
                        onOpenMenu = { menuAutomation = a },
                    )
                    Spacer(Modifier.height(8.dp))
                }
            }
            Spacer(Modifier.height(4.dp))
            SettingsButton(t.settingsAutomations.add, onClick = { mode = BuilderMode.New })

            Spacer(Modifier.height(8.dp))
            Hint(t.settingsAutomations.footer)
            Spacer(Modifier.height(24.dp))
        }
    } else if (mode is BuilderMode.History) {
        val automation = (mode as BuilderMode.History).automation
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp)) {
            SectionHeader("${t.settingsAutomations.historyTitle} · ${automation.name}", first = true)
            Panel {
                when {
                    !runsLoaded -> HbText(t.settingsAutomations.historyLoading, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
                    runs.isEmpty() -> HbText(t.settingsAutomations.historyEmpty, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
                    else -> runs.forEachIndexed { i, r ->
                        if (i > 0) Spacer(Modifier.height(10.dp))
                        RunRow(r)
                    }
                }
            }
            Spacer(Modifier.height(10.dp))
            SettingsButton(t.common.close, onClick = { mode = BuilderMode.Closed })
            Spacer(Modifier.height(24.dp))
        }
    } else {
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp)) {
            AutomationBuilder(
                existing = (mode as? BuilderMode.Editing)?.automation,
                agents = agents,
                onCancel = { mode = BuilderMode.Closed },
                onSave = { draft ->
                    val result = when (val m = mode) {
                        is BuilderMode.Editing -> api.updateAutomation(config, m.automation.id, draft)
                        else -> api.createAutomation(config, draft)
                    }
                    when (result) {
                        is AutomationSaveResult.Ok -> { mode = BuilderMode.Closed; reload(); null }
                        is AutomationSaveResult.Error -> t.settingsAutomations.saveFailed(result.message)
                    }
                },
            )
        }
    }

    menuAutomation?.let { a ->
        AutomationActionDialog(
            automation = a,
            onDismiss = { menuAutomation = null },
            onEdit = {
                menuAutomation = null
                mode = BuilderMode.Editing(a)
            },
            onHistory = {
                menuAutomation = null
                mode = BuilderMode.History(a)
            },
            onTest = {
                menuAutomation = null
                scope.launch {
                    testMsg = a.id to t.settingsAutomations.testSending
                    val ok = api.testAutomation(config, a.id)
                    testMsg = a.id to (if (ok) t.settingsAutomations.testSent else t.settingsAutomations.testFailed)
                    delay(3000)
                    if (testMsg?.first == a.id) testMsg = null
                }
            },
            onDelete = {
                menuAutomation = null
                autos = autos.filter { it.id != a.id }
                scope.launch { api.deleteAutomation(config, a.id); reload() }
            },
        )
    }
}

/** One run — status, when, and its report text clamped with a More/Less
 *  toggle, same clamp-and-expand pattern AgentCommsScreen's CommLine uses
 *  for long tool output. */
@Composable
private fun RunRow(r: AutomationRunInfo) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    var open by remember(r.id) { mutableStateOf(false) }
    val report = r.report.trim()
    val clip = 220
    val clipped = report.length > clip
    val shown = if (open || !clipped) report else "${report.take(clip)}…"
    val statusColor = when (r.status) {
        "ok" -> palette.accentBright
        "failed" -> palette.red
        else -> palette.textFaint
    }
    val statusLabel = when (r.status) {
        "ok" -> t.settingsAutomations.runStatusOk
        "failed" -> t.settingsAutomations.runStatusFailed
        "cancelled" -> t.settingsAutomations.runStatusCancelled
        else -> r.status
    }
    Column(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.size(8.dp).background(statusColor, RoundedCornerShape(50)))
            HbText(r.firedAt, style = HbType.readout.copy(fontSize = 10.sp), color = palette.text)
            HbText(
                statusLabel + if (r.channel == "voice") " · 🔊" else "",
                style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint,
            )
        }
        if (r.status == "ok" && r.channel != "silent" && !r.delivered) {
            Spacer(Modifier.height(4.dp))
            HbText(t.settingsAutomations.runNotDelivered, style = HbType.readout.copy(fontSize = 9.5.sp), color = palette.amber)
        }
        Spacer(Modifier.height(4.dp))
        HbText(
            if (report.isEmpty()) t.settingsAutomations.runNoReport else shown,
            style = HbType.read.copy(fontSize = 12.5.sp, lineHeight = 1.4.em),
            color = palette.textFaint,
        )
        if (clipped) {
            Spacer(Modifier.height(2.dp))
            HbText(
                if (open) t.settingsAutomations.runLess else t.settingsAutomations.runMore,
                style = HbType.readout.copy(fontSize = 10.sp),
                color = palette.accentBright,
                modifier = Modifier.clickable { open = !open },
            )
        }
    }
}

@Composable
private fun StatusLine(label: String, ok: Boolean, detail: String) {
    val palette = LocalHbPalette.current
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        StatusDot(ok = ok)
        HbText(label, style = HbType.readout.copy(fontSize = 10.sp), color = palette.text)
        HbText(detail, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint, maxLines = 1)
    }
}

@Composable
private fun AutomationCard(
    a: AutomationInfo,
    testLabel: String?,
    onToggle: (Boolean) -> Unit,
    onClick: () -> Unit,
    onOpenMenu: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val shape = RoundedCornerShape(12.dp)
    val cardFill = Color.White.copy(alpha = if (a.active) 0.035f else 0.015f)
    val cardBorder = if (a.active) Color.White.copy(alpha = 0.08f) else Color.White.copy(alpha = 0.04f)

    Row(
        Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(cardFill)
            .border(1.dp, cardBorder, shape)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(Modifier.weight(1f)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Box(
                    Modifier
                        .border(
                            width = 1.dp,
                            color = if (a.active) palette.accent.copy(alpha = 0.45f) else palette.textFaint.copy(alpha = 0.25f),
                            shape = RoundedCornerShape(4.dp),
                        )
                        .background(
                            if (a.active) palette.accent.copy(alpha = 0.12f) else Color.Transparent,
                            RoundedCornerShape(4.dp),
                        )
                        .padding(horizontal = 5.dp, vertical = 2.dp),
                ) {
                    HbText(
                        KIND_LABEL[a.kind] ?: a.kind.uppercase(),
                        style = HbType.readout.copy(fontSize = 9.sp, fontWeight = FontWeight.SemiBold),
                        color = if (a.active) palette.accentBright else palette.textFaint,
                    )
                }
                HbText(
                    a.name,
                    style = HbType.read.copy(fontSize = 14.sp, fontWeight = FontWeight.Medium),
                    color = if (a.active) palette.text else palette.textDim,
                    maxLines = 2,
                    modifier = Modifier.weight(1f, fill = false),
                )
            }
            val sub = testLabel ?: a.summary
            if (sub.isNotEmpty()) {
                Spacer(Modifier.height(4.dp))
                HbText(
                    sub,
                    style = HbType.readout.copy(fontSize = 10.5.sp),
                    color = when {
                        testLabel == t.settingsAutomations.testFailed -> palette.red
                        testLabel != null -> palette.accentBright
                        else -> palette.textFaint
                    },
                    maxLines = 1,
                )
            }
        }

        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            HbToggle(checked = a.active, color = palette.accent, onToggle = onToggle)
            Box(
                Modifier
                    .size(32.dp)
                    .clip(CircleShape)
                    .clickable(onClick = onOpenMenu),
                contentAlignment = Alignment.Center,
            ) {
                HbGlyphs.MoreVertical(palette.textDim, size = 15.dp)
            }
        }
    }
}

@Composable
private fun AutomationActionDialog(
    automation: AutomationInfo,
    onDismiss: () -> Unit,
    onEdit: () -> Unit,
    onHistory: () -> Unit,
    onTest: () -> Unit,
    onDelete: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    var confirmDelete by remember { mutableStateOf(false) }

    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(18.dp))
                .background(palette.base)
                .border(1.dp, palette.edge, RoundedCornerShape(18.dp))
                .padding(20.dp),
        ) {
            // Header: kind badge + title + close
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Box(
                    Modifier
                        .border(
                            width = 1.dp,
                            color = palette.accent.copy(alpha = 0.45f),
                            shape = RoundedCornerShape(4.dp),
                        )
                        .background(palette.accent.copy(alpha = 0.12f), RoundedCornerShape(4.dp))
                        .padding(horizontal = 5.dp, vertical = 2.dp),
                ) {
                    HbText(
                        KIND_LABEL[automation.kind] ?: automation.kind.uppercase(),
                        style = HbType.readout.copy(fontSize = 9.sp, fontWeight = FontWeight.SemiBold),
                        color = palette.accentBright,
                    )
                }
                HbText(
                    automation.name,
                    style = HbType.headerBar.copy(fontSize = 16.sp),
                    color = palette.text,
                    maxLines = 1,
                    modifier = Modifier.weight(1f),
                )
                Box(
                    Modifier
                        .size(30.dp)
                        .clip(CircleShape)
                        .clickable(onClick = onDismiss),
                    contentAlignment = Alignment.Center,
                ) {
                    HbGlyphs.Close(palette.textDim, size = 13.dp)
                }
            }

            if (automation.summary.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                HbText(
                    automation.summary,
                    style = HbType.readout.copy(fontSize = 11.5.sp),
                    color = palette.textFaint,
                )
            }

            Spacer(Modifier.height(14.dp))
            Box(Modifier.height(1.dp).fillMaxWidth().background(Color.White.copy(alpha = 0.07f)))
            Spacer(Modifier.height(6.dp))

            // Actions
            ActionSheetItem(
                label = t.settingsAutomations.edit,
                onClick = onEdit,
            )
            ActionSheetItem(
                label = t.settingsAutomations.test,
                onClick = onTest,
            )
            ActionSheetItem(
                label = t.settingsAutomations.history,
                onClick = onHistory,
            )

            Spacer(Modifier.height(6.dp))
            Box(Modifier.height(1.dp).fillMaxWidth().background(Color.White.copy(alpha = 0.07f)))
            Spacer(Modifier.height(10.dp))

            if (!confirmDelete) {
                ActionSheetItem(
                    label = t.common.delete,
                    textColor = palette.red,
                    onClick = { confirmDelete = true },
                )
            } else {
                Column(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp))
                        .background(palette.red.copy(alpha = 0.12f))
                        .border(1.dp, palette.red.copy(alpha = 0.35f), RoundedCornerShape(8.dp))
                        .padding(12.dp),
                ) {
                    HbText(
                        t.settingsAutomations.deleteWatcherTitle,
                        style = HbType.read.copy(fontSize = 12.sp),
                        color = palette.text,
                    )
                    Spacer(Modifier.height(10.dp))
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.End,
                    ) {
                        SettingsButton(t.common.cancel, onClick = { confirmDelete = false })
                        Spacer(Modifier.width(8.dp))
                        SettingsButton(t.common.delete, onClick = onDelete, tint = palette.red)
                    }
                }
            }
        }
    }
}

@Composable
private fun ActionSheetItem(
    label: String,
    textColor: Color = LocalHbPalette.current.text,
    onClick: () -> Unit,
) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        HbText(
            label,
            style = HbType.read.copy(fontSize = 14.sp),
            color = textColor,
            modifier = Modifier.weight(1f),
        )
        HbGlyphs.ChevronRight(palette.textFaint, size = 10.dp)
    }
}
