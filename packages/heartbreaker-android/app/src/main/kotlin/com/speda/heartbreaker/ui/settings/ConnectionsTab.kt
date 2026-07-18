
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
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun ConnectionsTab(config: AppConfig, graph: AppGraph) {
    val palette = LocalHbPalette.current
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
            onMsg("Opening sign-in…")
            val url = api.oauthLoginUrl(config, provider)
            if (url == null) { onMsg("Couldn't start sign-in."); return@launch }
            openUrl(context, url)
            onMsg("Finish in your browser, then come back — it connects automatically.")
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
        SectionHeader("Managed accounts")
        OAuthCard(
            name = "Google Workspace",
            desc = if (google) "Connected — Gmail, Calendar, Drive & Contacts are live." else "Connect for Gmail, Calendar and Drive.",
            connected = google,
            message = googleMsg,
            onConnect = { connect("google", { googleMsg = it }, { google = true }) },
            onDisconnect = { scope.launch { api.oauthDisconnect(config, "google"); google = false; googleMsg = ""; reload() } },
        )
        Spacer(Modifier.height(10.dp))
        OAuthCard(
            name = "Notion Workspace",
            desc = if (notion) "Connected — search, fetch and page tools are live." else "Connect your workspace for search, fetch and page creation.",
            connected = notion,
            message = notionMsg,
            onConnect = { connect("notion", { notionMsg = it }, { notion = true }) },
            onDisconnect = { scope.launch { api.oauthDisconnect(config, "notion"); notion = false; notionMsg = ""; reload() } },
        )

        SectionHeader("Tool budget")
        Panel {
            val used = data.activeToolTokens
            val limit = data.itpmLimit.coerceAtLeast(1)
            val pct = (used.toFloat() / limit).coerceIn(0f, 1f)
            val over = used > limit
            val col = if (over) palette.red else if (pct > 0.8f) palette.amber else palette.green
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                HbText("ACTIVE TOOL TOKENS", style = HbType.readout.copy(fontSize = 10.sp), color = palette.textDim)
                HbText("~$used / $limit", style = HbType.readout.copy(fontSize = 10.sp), color = col)
            }
            Spacer(Modifier.height(6.dp))
            Box(Modifier.fillMaxWidth().height(6.dp).background(palette.accent.copy(alpha = 0.12f))) {
                Box(Modifier.fillMaxWidth(pct).height(6.dp).background(col))
            }
            if (over) {
                Spacer(Modifier.height(6.dp))
                HbText("Over the cold-write limit — disable a server (Notion is heaviest).", style = HbType.readout.copy(fontSize = 10.sp), color = palette.red)
            }
        }

        SectionHeader("Toolsets")
        Panel {
            if (data.servers.isEmpty()) {
                HbText("No MCP servers loaded.", style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
            } else {
                data.servers.forEachIndexed { i, c ->
                    if (i > 0) Spacer(Modifier.height(6.dp))
                    ServerRow(c) { active ->
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
    Panel {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Column(Modifier.weight(1f)) {
                HbText(name, style = HbType.read.copy(fontSize = 14.sp, fontWeight = FontWeight.SemiBold), color = palette.text)
                HbText(desc, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
            }
            if (connected) {
                SettingsButton("Disconnect", onClick = onDisconnect, tint = palette.textDim)
            } else {
                SettingsButton("Connect", onClick = onConnect)
            }
        }
        if (message.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            HbText(message, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textDim)
        }
    }
}

@Composable
private fun ServerRow(c: ConnectionInfo, onToggle: (Boolean) -> Unit) {
    val palette = LocalHbPalette.current
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        StatusDot(ok = c.connected, warnColor = palette.red)
        Column(Modifier.weight(1f)) {
            HbText(c.label.ifEmpty { c.server }, style = HbType.read.copy(fontSize = 13.5.sp), color = palette.text, maxLines = 1)
            HbText(
                if (c.connected) "${c.tools} tools · ${if (c.alwaysOn) "always on" else "on demand"}"
                else (c.needs?.let { "needs $it" } ?: "offline"),
                style = HbType.readout.copy(fontSize = 10.sp),
                color = palette.textFaint,
                maxLines = 1,
            )
        }
        HbToggle(checked = c.active && c.connected, enabled = c.connected, color = palette.accent, onToggle = onToggle)
    }
}