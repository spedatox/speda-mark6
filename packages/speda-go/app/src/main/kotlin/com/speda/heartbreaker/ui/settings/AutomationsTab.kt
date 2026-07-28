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
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.AutomationInfo
import com.speda.heartbreaker.data.AutomationsStatus
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private val KIND_LABEL = mapOf("web_watch" to "WEB", "rss_watch" to "RSS", "schedule" to "CRON", "webhook" to "HOOK")

@Composable
fun AutomationsTab(config: AppConfig, graph: AppGraph) {
    val palette = LocalHbPalette.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val api = graph.api

    var autos by remember { mutableStateOf<List<AutomationInfo>>(emptyList()) }
    var status by remember { mutableStateOf<AutomationsStatus?>(null) }
    var tgMsg by remember { mutableStateOf("") }

    suspend fun reload() {
        autos = api.getAutomations(config)
        status = api.getAutomationsStatus(config)
    }
    LaunchedEffect(config) { reload() }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        SectionHeader("Pipeline")
        Panel {
            val s = status
            StatusLine(
                "n8n ENGINE",
                ok = s?.n8nOnline == true,
                detail = when {
                    s == null -> "checking…"
                    !s.n8nConfigured -> "needs N8N_API_KEY in the backend .env"
                    s.n8nOnline -> s.n8nUrl
                    else -> "unreachable — is the n8n container running?"
                },
            )
            Spacer(Modifier.height(6.dp))
            StatusLine(
                "TELEGRAM DELIVERY",
                ok = s?.telegramConnected == true,
                detail = when {
                    s == null -> "checking…"
                    !s.telegramConfigured -> "needs TELEGRAM_BOT_TOKEN in the backend .env"
                    s.telegramConnected -> "connected — SPEDA can reach you"
                    else -> "bot ready — connect your chat below"
                },
            )
            if (s?.telegramConfigured == true && !s.telegramConnected) {
                Spacer(Modifier.height(10.dp))
                SettingsButton("Connect Telegram", onClick = {
                    scope.launch {
                        tgMsg = "Opening Telegram…"
                        val link = api.telegramConnect(config)
                        if (link == null) { tgMsg = "Couldn't start the connect flow."; return@launch }
                        openUrl(context, link)
                        tgMsg = "Tap START in Telegram — connecting…"
                        repeat(40) {
                            delay(3000)
                            if (api.telegramConnected(config)) { tgMsg = ""; reload(); return@launch }
                        }
                        tgMsg = "No response yet — try Connect again."
                    }
                })
                if (tgMsg.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    HbText(tgMsg, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textDim)
                }
            }
        }

        SectionHeader("Watchers")
        Panel {
            if (autos.isEmpty()) {
                HbText("Nothing is being watched yet.", style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
            } else {
                autos.forEachIndexed { i, a ->
                    if (i > 0) Spacer(Modifier.height(8.dp))
                    WatcherRow(
                        a,
                        onToggle = { active ->
                            autos = autos.map { if (it.id == a.id) it.copy(active = active) else it }
                            scope.launch { api.toggleAutomation(config, a.id, active); reload() }
                        },
                        onDelete = {
                            autos = autos.filter { it.id != a.id }
                            scope.launch { api.deleteAutomation(config, a.id); reload() }
                        },
                    )
                }
            }
        }

        Spacer(Modifier.height(8.dp))
        Hint("SPEDA creates these itself — just ask: “track this page for a month and tell me when my results are up”. When a watcher fires it pings you on Telegram.")
        Spacer(Modifier.height(24.dp))
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
private fun WatcherRow(a: AutomationInfo, onToggle: (Boolean) -> Unit, onDelete: () -> Unit) {
    val palette = LocalHbPalette.current
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
        Column(Modifier.weight(1f)) {
            HbText(a.name, style = HbType.read.copy(fontSize = 13.5.sp, fontWeight = FontWeight.Medium), color = palette.text, maxLines = 1)
            HbText(a.summary, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint, maxLines = 1)
        }
        HbToggle(checked = a.active, color = palette.accent, onToggle = onToggle)
        Box(
            Modifier.size(26.dp).clickable(onClick = onDelete),
            contentAlignment = Alignment.Center,
        ) { HbGlyphs.Close(palette.textFaint, size = 12.dp) }
    }
}