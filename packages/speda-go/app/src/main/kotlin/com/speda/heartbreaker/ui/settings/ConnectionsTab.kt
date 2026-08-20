
package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
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
import com.speda.heartbreaker.data.ConnectionInfo
import com.speda.heartbreaker.data.ConnectionsResult
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.AppStrings
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun ConnectionsTab(config: AppConfig, graph: AppGraph) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val api = graph.api

    var data by remember { mutableStateOf(ConnectionsResult()) }
    var google by remember { mutableStateOf(false) }
    var notion by remember { mutableStateOf(false) }
    var googleMsg by remember { mutableStateOf("") }
    var notionMsg by remember { mutableStateOf("") }

    suspend fun reload() { data = api.getConnections(config) }
    LaunchedEffect(config) {
        reload()
        google = api.oauthStatus(config, "google")
        notion = api.oauthStatus(config, "notion")
    }

    fun connect(provider: String, onMsg: (String) -> Unit, onDone: () -> Unit) {
        scope.launch {
            onMsg(t.settingsConnections.openingSignIn)
            val url = api.oauthLoginUrl(config, provider)
            if (url == null) { onMsg(t.settingsConnections.couldntStartSignIn); return@launch }
            openUrl(context, url)
            onMsg(t.settingsConnections.finishInBrowser)
            repeat(20) {
                delay(3000)
                reload()
                if (api.oauthStatus(config, provider)) { onDone(); onMsg(""); return@launch }
            }
        }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        SectionHeader(t.settingsConnections.managedAccounts)
        OAuthCard(
            name = "Google Workspace",
            desc = if (google) t.settingsConnections.googleConnected else t.settingsConnections.googleDisconnected,
            connected = google,
            message = googleMsg,
            onConnect = { connect("google", { googleMsg = it }, { google = true }) },
            onDisconnect = { scope.launch { api.oauthDisconnect(config, "google"); google = false; googleMsg = ""; reload() } },
        )
        Spacer(Modifier.height(10.dp))
        OAuthCard(
            name = "Notion Workspace",
            desc = if (notion) t.settingsConnections.notionConnected else t.settingsConnections.notionDisconnected,
            connected = notion,
            message = notionMsg,
            onConnect = { connect("notion", { notionMsg = it }, { notion = true }) },
            onDisconnect = { scope.launch { api.oauthDisconnect(config, "notion"); notion = false; notionMsg = ""; reload() } },
        )

        SectionHeader(t.settingsConnections.toolBudget)
        Panel {
            val used = data.activeToolTokens
            val limit = data.itpmLimit.coerceAtLeast(1)
            val pct = (used.toFloat() / limit).coerceIn(0f, 1f)
            val over = used > limit
            val col = if (over) palette.red else if (pct > 0.8f) palette.amber else palette.green
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                HbText(t.settingsConnections.activeToolTokens, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textDim)
                HbText("~$used / $limit", style = HbType.readout.copy(fontSize = 10.sp), color = col)
            }
            Spacer(Modifier.height(6.dp))
            Box(Modifier.fillMaxWidth().height(6.dp).background(palette.accent.copy(alpha = 0.12f))) {
                Box(Modifier.fillMaxWidth(pct).height(6.dp).background(col))
            }
            if (over) {
                Spacer(Modifier.height(6.dp))
                HbText(t.settingsConnections.overLimit, style = HbType.readout.copy(fontSize = 10.sp), color = palette.red)
            }
        }

        SectionHeader(t.settingsConnections.toolsets)
        Panel {
            if (data.servers.isEmpty()) {
                HbText(t.settingsConnections.noMcpServers, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
            } else {
                data.servers.forEachIndexed { i, c ->
                    if (i > 0) Spacer(Modifier.height(6.dp))
                    ServerRow(c, t) { active ->
                        data = data.copy(servers = data.servers.map { if (it.server == c.server) it.copy(active = active) else it })
                        scope.launch { api.setConnection(config, c.server, active); reload() }
                    }
                }
            }
        }

        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun OAuthCard(
    name: String,
    desc: String,
    connected: Boolean,
    message: String,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    Panel {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Column(Modifier.weight(1f)) {
                HbText(name, style = HbType.read.copy(fontSize = 14.sp, fontWeight = FontWeight.SemiBold), color = palette.text)
                HbText(desc, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
            }
            if (connected) {
                SettingsButton(t.common.disconnect, onClick = onDisconnect, tint = palette.textDim)
            } else {
                SettingsButton(t.common.connect, onClick = onConnect)
            }
        }
        if (message.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            HbText(message, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textDim)
        }
    }
}

@Composable
private fun ServerRow(c: ConnectionInfo, t: AppStrings, onToggle: (Boolean) -> Unit) {
    val palette = LocalHbPalette.current
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        StatusDot(ok = c.connected, warnColor = palette.red)
        Column(Modifier.weight(1f)) {
            HbText(c.label.ifEmpty { c.server }, style = HbType.read.copy(fontSize = 13.5.sp), color = palette.text, maxLines = 1)
            HbText(
                if (c.connected) (if (c.alwaysOn) t.settingsConnections.toolsAlwaysOn(c.tools) else t.settingsConnections.toolsOnDemand(c.tools))
                else (c.needs?.let { t.settingsConnections.needs(it) } ?: t.settingsConnections.offline),
                style = HbType.readout.copy(fontSize = 10.sp),
                color = palette.textFaint,
                maxLines = 1,
            )
        }
        HbToggle(checked = c.active && c.connected, enabled = c.connected, color = palette.accent, onToggle = onToggle)
    }
}