
package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.background
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
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.ConnectionInfo
import com.speda.heartbreaker.data.ConnectionsResult
import com.speda.heartbreaker.data.CustomMcpResult
import com.speda.heartbreaker.data.CustomMcpServer
import com.speda.heartbreaker.data.Portal
import com.speda.heartbreaker.data.PortalsResult
import com.speda.heartbreaker.data.commandLine
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
    var microsoft by remember { mutableStateOf(false) }
    var googleMsg by remember { mutableStateOf("") }
    var notionMsg by remember { mutableStateOf("") }
    var microsoftMsg by remember { mutableStateOf("") }

    var mcp by remember { mutableStateOf(CustomMcpResult()) }
    var portals by remember { mutableStateOf(PortalsResult()) }
    var addingServer by remember { mutableStateOf(false) }
    var addingPortal by remember { mutableStateOf(false) }

    suspend fun reload() { data = api.getConnections(config) }
    suspend fun reloadMcp() { mcp = api.getCustomMcpServers(config) }
    suspend fun reloadPortals() { portals = api.getPortals(config) }
    LaunchedEffect(config) {
        reload()
        google = api.oauthStatus(config, "google")
        notion = api.oauthStatus(config, "notion")
        microsoft = api.oauthStatus(config, "microsoft")
        reloadMcp()
        reloadPortals()
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
        Spacer(Modifier.height(10.dp))
        OAuthCard(
            name = "Microsoft Graph",
            desc = if (microsoft) t.settingsConnections.microsoftConnected else t.settingsConnections.microsoftDisconnected,
            connected = microsoft,
            message = microsoftMsg,
            onConnect = { connect("microsoft", { microsoftMsg = it }, { microsoft = true }) },
            onDisconnect = { scope.launch { api.oauthDisconnect(config, "microsoft"); microsoft = false; microsoftMsg = ""; reload() } },
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

        // ── Custom MCP servers — a Tier-2 capability the owner wires up without
        // a code change. A DIFFERENT list from Toolsets above: those toggle the
        // engine's own managed servers, these are the owner's.
        SectionHeader(t.settingsConnections.customServers)
        Panel {
            if (mcp.servers.isEmpty() && !addingServer) {
                HbText(t.settingsConnections.noCustomServers, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
            } else {
                mcp.servers.forEachIndexed { i, s ->
                    if (i > 0) Spacer(Modifier.height(6.dp))
                    McpServerRow(s) {
                        scope.launch { api.deleteCustomMcpServer(config, s.name); reloadMcp() }
                    }
                }
            }
            Spacer(Modifier.height(if (mcp.servers.isEmpty() && !addingServer) 0.dp else 10.dp))
            if (addingServer) {
                AddMcpServerForm(
                    reserved = mcp.reserved,
                    onCancel = { addingServer = false },
                    onSave = { name, transport, command, url, note ->
                        scope.launch {
                            val res = api.saveCustomMcpServer(config, name, transport, command, url, emptyMap(), emptyMap(), true, note)
                            if (res.error == null) { addingServer = false; reloadMcp() }
                        }
                    },
                )
            } else {
                Row {
                    SettingsButton(t.settingsConnections.addServer, onClick = { addingServer = true })
                }
            }
        }

        // ── Web portals — the owner's saved logins. The container keeps the
        // cookies, this app keeps the credentials; a password never reaches a
        // model (CLAUDE.md, Security).
        SectionHeader(t.settingsConnections.portalsSection)
        Panel {
            val browser = portals.browser
            when (browser.status) {
                "ok" -> {}
                "off" -> HbText(t.settingsConnections.browserContainerOff, style = HbType.readout.copy(fontSize = 10.5.sp), color = palette.textFaint)
                else -> HbText(
                    t.settingsConnections.browserContainerDown(browser.reason ?: browser.status),
                    style = HbType.readout.copy(fontSize = 10.5.sp),
                    color = palette.amber,
                )
            }
            if (browser.status != "ok") Spacer(Modifier.height(8.dp))

            if (portals.portals.isEmpty() && !addingPortal) {
                HbText(t.settingsConnections.noPortals, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
            } else {
                portals.portals.forEachIndexed { i, p ->
                    if (i > 0) Spacer(Modifier.height(6.dp))
                    PortalRow(
                        portal = p,
                        canLogin = browser.status == "ok",
                        onLogin = { scope.launch { api.portalLogin(config, p.name); reloadPortals() } },
                        onForget = { scope.launch { api.portalForget(config, p.name); reloadPortals() } },
                        onDelete = { scope.launch { api.deletePortal(config, p.name); reloadPortals() } },
                    )
                }
            }
            Spacer(Modifier.height(if (portals.portals.isEmpty() && !addingPortal) 0.dp else 10.dp))
            if (addingPortal) {
                AddPortalForm(
                    onCancel = { addingPortal = false },
                    onSave = { name, label, loginUrl, homeUrl, username, password, note ->
                        scope.launch {
                            val res = api.savePortal(config, name, label, loginUrl, homeUrl, username, password, note, true, emptyList(), false)
                            if (res.error == null) { addingPortal = false; reloadPortals() }
                        }
                    },
                )
            } else {
                Row {
                    SettingsButton(t.settingsConnections.addPortal, onClick = { addingPortal = true })
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

@Composable
private fun McpServerRow(s: CustomMcpServer, onDelete: () -> Unit) {
    val palette = LocalHbPalette.current
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        StatusDot(ok = s.connected == true, warnColor = palette.red)
        Column(Modifier.weight(1f)) {
            HbText(s.name, style = HbType.read.copy(fontSize = 13.5.sp), color = palette.text, maxLines = 1)
            val detail = if (s.transport == "http") s.url else s.commandLine()
            HbText(detail, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint, maxLines = 1)
        }
        SettingsButton(LocalStrings.current.common.delete, onClick = onDelete, tint = palette.red)
    }
}

@Composable
private fun AddMcpServerForm(
    reserved: List<String>,
    onCancel: () -> Unit,
    onSave: (name: String, transport: String, command: String, url: String, note: String) -> Unit,
) {
    val t = LocalStrings.current
    var name by remember { mutableStateOf("") }
    var transport by remember { mutableStateOf("stdio") }
    var command by remember { mutableStateOf("") }
    var url by remember { mutableStateOf("") }
    var note by remember { mutableStateOf("") }
    val nameClash = name.isNotBlank() && reserved.contains(name.trim())

    Column {
        FieldLabel("Name")
        GlassField(name, { name = it }, placeholder = "my-server", singleLine = true)
        if (nameClash) HbText("This name is reserved by a built-in tier.", style = HbType.readout.copy(fontSize = 10.sp), color = LocalHbPalette.current.red)
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TransportChip("stdio", selected = transport == "stdio") { transport = "stdio" }
            TransportChip("http", selected = transport == "http") { transport = "http" }
        }
        Spacer(Modifier.height(8.dp))
        if (transport == "stdio") {
            FieldLabel("Command")
            GlassField(command, { command = it }, placeholder = "npx -y @scope/server", singleLine = true, mono = true)
        } else {
            FieldLabel("URL")
            GlassField(url, { url = it }, placeholder = "https://example.com/mcp", singleLine = true, mono = true)
        }
        Spacer(Modifier.height(8.dp))
        FieldLabel("Note")
        GlassField(note, { note = it }, placeholder = "", singleLine = true)
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SettingsButton(t.common.save, enabled = name.isNotBlank() && !nameClash, onClick = { onSave(name.trim(), transport, command, url, note) })
            SettingsButton(t.common.cancel, onClick = onCancel, tint = LocalHbPalette.current.textDim)
        }
    }
}

@Composable
private fun TransportChip(label: String, selected: Boolean, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Box(
        Modifier
            .background(if (selected) palette.accent.copy(alpha = 0.16f) else palette.text.copy(alpha = 0.03f))
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
    ) {
        HbText(label, style = HbType.readout.copy(fontSize = 11.sp), color = if (selected) palette.accentBright else palette.textFaint)
    }
}

@Composable
private fun PortalRow(
    portal: Portal,
    canLogin: Boolean,
    onLogin: () -> Unit,
    onForget: () -> Unit,
    onDelete: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        StatusDot(ok = portal.session == true, warnColor = palette.textFaint)
        Column(Modifier.weight(1f)) {
            HbText(portal.label.ifEmpty { portal.name }, style = HbType.read.copy(fontSize = 13.5.sp), color = palette.text, maxLines = 1)
            HbText(
                if (portal.session == true) t.settingsConnections.hasSession else t.settingsConnections.noSession,
                style = HbType.readout.copy(fontSize = 10.sp),
                color = palette.textFaint,
                maxLines = 1,
            )
        }
        if (portal.session == true) {
            SettingsButton(t.settingsConnections.forgetSession, onClick = onForget, tint = palette.textDim)
        } else {
            SettingsButton(t.settingsConnections.signIn, enabled = canLogin, onClick = onLogin)
        }
        SettingsButton(t.common.delete, onClick = onDelete, tint = palette.red)
    }
}

@Composable
private fun AddPortalForm(
    onCancel: () -> Unit,
    onSave: (name: String, label: String, loginUrl: String, homeUrl: String, username: String, password: String, note: String) -> Unit,
) {
    val t = LocalStrings.current
    var name by remember { mutableStateOf("") }
    var label by remember { mutableStateOf("") }
    var loginUrl by remember { mutableStateOf("") }
    var homeUrl by remember { mutableStateOf("") }
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var note by remember { mutableStateOf("") }
    var showPassword by remember { mutableStateOf(false) }

    Column {
        FieldLabel("Name")
        GlassField(name, { name = it }, placeholder = "my-portal", singleLine = true)
        Spacer(Modifier.height(8.dp))
        FieldLabel("Label")
        GlassField(label, { label = it }, placeholder = "", singleLine = true)
        Spacer(Modifier.height(8.dp))
        FieldLabel("Login URL")
        GlassField(loginUrl, { loginUrl = it }, placeholder = "https://example.com/login", singleLine = true, mono = true)
        Spacer(Modifier.height(8.dp))
        FieldLabel("Home URL")
        GlassField(homeUrl, { homeUrl = it }, placeholder = "https://example.com", singleLine = true, mono = true)
        Spacer(Modifier.height(8.dp))
        FieldLabel("Username")
        GlassField(username, { username = it }, placeholder = "", singleLine = true)
        Spacer(Modifier.height(8.dp))
        FieldLabel("Password")
        GlassField(
            password, { password = it }, placeholder = "", singleLine = true,
            visualTransformation = if (showPassword) VisualTransformation.None else PasswordVisualTransformation(),
        )
        Spacer(Modifier.height(4.dp))
        Row {
            SettingsButton(if (showPassword) t.common.hide else t.common.show, onClick = { showPassword = !showPassword }, tint = LocalHbPalette.current.textDim)
        }
        Spacer(Modifier.height(8.dp))
        FieldLabel("Note")
        GlassField(note, { note = it }, placeholder = "", singleLine = true)
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SettingsButton(
                t.common.save,
                enabled = name.isNotBlank() && loginUrl.isNotBlank(),
                onClick = { onSave(name.trim(), label, loginUrl, homeUrl, username, password, note) },
            )
            SettingsButton(t.common.cancel, onClick = onCancel, tint = LocalHbPalette.current.textDim)
        }
    }
}
