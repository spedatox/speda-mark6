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
import androidx.compose.foundation.rememberScrollState
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
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
            Panel {
                if (autos.isEmpty()) {
                    HbText(t.settingsAutomations.nothingWatched, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
                } else {
                    autos.forEachIndexed { i, a ->
                        if (i > 0) Spacer(Modifier.height(8.dp))
                        WatcherRow(
                            a,
                            testLabel = testMsg?.takeIf { it.first == a.id }?.second,
                            onToggle = { active ->
                                autos = autos.map { if (it.id == a.id) it.copy(active = active) else it }
                                scope.launch { api.toggleAutomation(config, a.id, active); reload() }
                            },
                            onDelete = {
                                autos = autos.filter { it.id != a.id }
                                scope.launch { api.deleteAutomation(config, a.id); reload() }
                            },
                            onEdit = { mode = BuilderMode.Editing(a) },
                            onHistory = { mode = BuilderMode.History(a) },
                            onTest = {
                                scope.launch {
                                    testMsg = a.id to t.settingsAutomations.testSending
                                    val ok = api.testAutomation(config, a.id)
                                    testMsg = a.id to (if (ok) t.settingsAutomations.testSent else t.settingsAutomations.testFailed)
                                    delay(3000)
                                    if (testMsg?.first == a.id) testMsg = null
                                }
                            },
                        )
                    }
                }
                Spacer(Modifier.height(10.dp))
                SettingsButton(t.settingsAutomations.add, onClick = { mode = BuilderMode.New })
            }

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
private fun WatcherRow(
    a: AutomationInfo,
    testLabel: String?,
    onToggle: (Boolean) -> Unit,
    onDelete: () -> Unit,
    onEdit: () -> Unit,
    onHistory: () -> Unit,
    onTest: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    Column(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(
                Modifier
                    .border(1.dp, palette.accent.copy(alpha = 0.3f), RoundedCornerShape(3.dp))
                    .padding(horizontal = 5.dp, vertical = 1.dp),
            ) {
                HbText(
                    KIND_LABEL[a.kind] ?: a.kind.uppercase(),
                    style = HbType.readout.copy(fontSize = 8.5.sp),
                    color = if (a.active) palette.accentBright else palette.textFaint,
                )
            }
            Column(Modifier.weight(1f).clickable(onClick = onEdit)) {
                HbText(a.name, style = HbType.read.copy(fontSize = 13.5.sp, fontWeight = FontWeight.Medium), color = palette.text, maxLines = 1)
                HbText(a.summary, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint, maxLines = 1)
            }
            HbToggle(checked = a.active, color = palette.accent, onToggle = onToggle)
            Box(
                Modifier.size(26.dp).clickable(onClick = onDelete),
                contentAlignment = Alignment.Center,
            ) { HbGlyphs.Close(palette.textFaint, size = 12.dp) }
        }
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
            SettingsButton(t.settingsAutomations.edit, onClick = onEdit)
            SettingsButton(t.settingsAutomations.history, onClick = onHistory)
            SettingsButton(t.settingsAutomations.test, enabled = testLabel == null, onClick = onTest)
            testLabel?.let {
                HbText(
                    it,
                    style = HbType.readout.copy(fontSize = 10.sp),
                    color = if (it == t.settingsAutomations.testFailed) palette.red else palette.textFaint,
                )
            }
        }
    }
}
