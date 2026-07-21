package com.speda.heartbreaker.ui.systems

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Canvas
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
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.AgentModelInfo
import com.speda.heartbreaker.data.ConnectionsResult
import com.speda.heartbreaker.data.Health
import com.speda.heartbreaker.data.HbSettings
import com.speda.heartbreaker.data.ModelInfo
import com.speda.heartbreaker.data.OnlineAgent
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbText
import com.speda.heartbreaker.ui.settings.Panel
import com.speda.heartbreaker.ui.settings.SectionHeader
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * SYSTEMS 56A. — mobile port of SystemsBoard.tsx: uplink telemetry, network
 * nodes, the CORE ROUTING_MATRIX (model routing + MCP toolsets + per-agent
 * cores), TOKEN_BUDGET gauge, RESPONSE_TRACE sparkline and the DATA_BANKS
 * knowledge bank — reflowed into a single scrollable column (the mobile spec
 * per the port plan §1: "single-column Systems Board"). Every value comes
 * from the backend; nothing here is set dressing.
 */

private const val UPLINK_POLL_MS = 4000L
private const val PEERS_POLL_MS = 10_000L
private const val RTT_SAMPLES = 32

@Composable
fun SystemsBoardScreen(
    config: AppConfig,
    graph: AppGraph,
    settings: HbSettings,
    sessionCount: Int,
    onClose: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()
    val api = graph.api

    val health by remember(config) { graph.health.poll(config.apiBase, config.apiKey, UPLINK_POLL_MS) }
        .collectAsStateWithLifecycle(initialValue = Health.Offline)

    var rtt by remember { mutableStateOf<List<Long>>(emptyList()) }
    LaunchedEffect(health.latencyMs) {
        health.latencyMs?.let { v -> rtt = (rtt + v).takeLast(RTT_SAMPLES) }
    }

    var onlineAgents by remember { mutableStateOf<List<OnlineAgent>>(emptyList()) }
    LaunchedEffect(config) {
        while (true) {
            onlineAgents = api.fetchOnlineAgents(config)
            delay(PEERS_POLL_MS)
        }
    }
    val forgePeer = onlineAgents.any { it.agentId == "optimus" }

    var models by remember { mutableStateOf<List<ModelInfo>>(emptyList()) }
    var servers by remember { mutableStateOf(ConnectionsResult()) }
    var budgetMode by remember { mutableStateOf(true) }
    var agentInfos by remember { mutableStateOf<List<AgentModelInfo>>(emptyList()) }

    fun reloadConnections() {
        scope.launch { servers = api.getConnections(config) }
    }

    LaunchedEffect(config) {
        models = api.fetchModels(config)
        agentInfos = api.fetchAgentModels(config)
        budgetMode = api.getBudgetMode(config)
        servers = api.getConnections(config)
    }

    val ollamaUp = models.any { it.provider == "ollama" }
    val pct = if (servers.itpmLimit > 0) (servers.activeToolTokens * 100 / servers.itpmLimit.coerceAtLeast(1)) else 0
    val gaugeColor = if (pct > 100) palette.red else if (pct > 70) palette.amber else palette.green

    BackHandler(onBack = onClose)

    Column(
        Modifier
            .fillMaxSize()
            .background(palette.base)
            .statusBarsPadding()
            .navigationBarsPadding(),
    ) {
        // ── Title plate — "SYSTEMS 56A." convention, close on the left like
        // the settings sheet (the app's established full-screen overlay shape) ─
        Row(
            Modifier.fillMaxWidth().height(44.dp).padding(horizontal = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Box(
                Modifier.size(30.dp).hbGlass(shape = HbGlassShape.R9).clickable(onClick = onClose),
                contentAlignment = Alignment.Center,
            ) { HbGlyphs.Close(palette.iconBright, size = 13.dp) }
            HbText("SYSTEMS 56A.", style = HbType.headerBar.copy(fontSize = 13.sp), color = Color.White, caps = true)
            Spacer(Modifier.weight(1f))
            HbText("MK VI", style = HbType.headerBar.copy(fontSize = 10.sp), color = palette.accentDim, caps = true)
        }

        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 14.dp, vertical = 2.dp)) {
            SectionHeader("Uplink status")
            Panel {
                KV("LINK", if (health.online) "ONLINE" else "DENY", if (health.online) palette.green else palette.red)
                KV("HOST", config.apiBase.removePrefix("https://").removePrefix("http://"), palette.textDim, alt = true)
                KV("RTT", health.latencyMs?.let { "${it}ms" } ?: "--", if ((health.latencyMs ?: Long.MAX_VALUE) < 400) palette.green else palette.amber)
                KV("TOOLS REG.", health.tools?.toString() ?: "--", palette.textDim, alt = true)
                KV("SESSIONS", sessionCount.toString().padStart(3, '0'), palette.textDim)
                KV("BUDGET MODE", if (budgetMode) "ENGAGED" else "OFF", if (budgetMode) palette.amber else palette.textFaint, alt = true)
                KV("OLLAMA NODE", if (ollamaUp) "LOCAL ACTIVE" else "NOT DETECTED", if (ollamaUp) palette.green else palette.textFaint)
                KV("FORGE LINK", if (forgePeer) "OPTIMUS · MK II" else "IN-PROCESS", if (forgePeer) palette.green else palette.textFaint, alt = true)
            }

            SectionHeader("Network nodes")
            Panel {
                if (servers.servers.isEmpty()) {
                    HbText("// NO NODES", style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
                } else {
                    servers.servers.forEachIndexed { i, c ->
                        if (i > 0) Spacer(Modifier.height(6.dp))
                        Row(
                            Modifier.fillMaxWidth().padding(start = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Box(
                                Modifier
                                    .padding(end = 8.dp)
                                    .size(width = 2.dp, height = 22.dp)
                                    .background(
                                        if (!c.connected) palette.red.copy(alpha = 0.55f)
                                        else if (c.active) palette.accent.copy(alpha = 0.55f)
                                        else palette.accent.copy(alpha = 0.18f),
                                    ),
                            )
                            Column(Modifier.weight(1f)) {
                                HbText(c.label.ifEmpty { c.server }, style = HbType.readout.copy(fontSize = 11.sp), color = if (c.connected) palette.textDim else Color(0xFF7D6660), caps = true, maxLines = 1)
                                HbText(
                                    if (!c.connected) "MEDIA DISCONNECTED" else if (c.active) "LINKED · ${c.tools} TOOLS" else "STANDBY",
                                    style = HbType.readout.copy(fontSize = 9.sp),
                                    color = if (!c.connected) palette.red else if (c.active) palette.accent else palette.icon,
                                )
                            }
                        }
                    }
                }
            }

            SectionHeader("Core routing matrix")
            RoutingMatrix(
                models = models,
                servers = servers,
                activeModel = settings.model,
                onSelectModel = { m -> scope.launch { graph.settings.setModel(m) } },
                onToggleServer = { c ->
                    scope.launch { api.setConnection(config, c.server, !c.active); reloadConnections() }
                },
                agentInfos = agentInfos,
                onPinAgentModel = { agentId, model ->
                    scope.launch {
                        val infos = api.pinAgentModel(config, agentId, model)
                        if (infos.isNotEmpty()) agentInfos = infos
                    }
                },
            )

            SectionHeader("Token budget")
            Panel {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.Bottom) {
                    HbText("${pct}%", style = HbType.headerBar.copy(fontSize = 28.sp), color = gaugeColor)
                    HbText("PREFIX\nSATURATION", style = HbType.readout.copy(fontSize = 9.sp), color = palette.icon)
                }
                Spacer(Modifier.height(6.dp))
                SegBar(pct = pct, color = gaugeColor)
                HbText(
                    "~${servers.activeToolTokens} / ${servers.itpmLimit} ITPM",
                    style = HbType.readout.copy(fontSize = 9.sp),
                    color = palette.icon,
                    modifier = Modifier.padding(top = 6.dp, bottom = 8.dp),
                )
                val maxTok = (servers.servers.maxOfOrNull { it.tools } ?: 1).coerceAtLeast(1)
                servers.servers.sortedByDescending { it.tools }.take(5).forEach { s ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        HbText(s.server, style = HbType.readout.copy(fontSize = 9.sp), color = if (s.active) palette.iconBright else palette.iconDim, caps = true, maxLines = 1, modifier = Modifier.padding(end = 2.dp))
                        Box(Modifier.weight(1f).height(4.dp).background(palette.accent.copy(alpha = 0.1f))) {
                            val frac = (s.tools.toFloat() / maxTok).coerceIn(0f, 1f)
                            Box(Modifier.fillMaxWidth(frac).height(4.dp).background(if (s.active) palette.accent.copy(alpha = 0.6f) else palette.accent.copy(alpha = 0.25f)))
                        }
                    }
                }
            }

            SectionHeader("Response trace")
            Panel {
                RttSpark(samples = rtt)
                Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                    HbText("RTT / 4s PROBE", style = HbType.readout.copy(fontSize = 9.sp), color = palette.icon)
                    HbText(health.latencyMs?.let { "${it}ms" } ?: "--", style = HbType.readout.copy(fontSize = 9.sp), color = palette.accent)
                }
            }

            SectionHeader("Data banks // knowledge")
            KnowledgeBank(config = config, api = api)

            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun KV(k: String, v: String, color: Color, alt: Boolean = false) {
    val palette = LocalHbPalette.current
    Row(
        Modifier.fillMaxWidth().background(if (alt) palette.accent.copy(alpha = 0.04f) else Color.Transparent).padding(vertical = 3.dp, horizontal = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        HbText(k, style = HbType.readout.copy(fontSize = 10.sp), color = palette.icon, caps = true)
        HbText(v, style = HbType.readout.copy(fontSize = 10.sp), color = color, maxLines = 1)
    }
}

@Composable
private fun SegBar(pct: Int, color: Color) {
    val palette = LocalHbPalette.current
    val segs = 22
    val lit = (pct.coerceIn(0, 100) * segs / 100)
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        repeat(segs) { i ->
            Box(
                Modifier.weight(1f).height(7.dp).background(if (i < lit) color else palette.accent.copy(alpha = 0.14f)),
            )
        }
    }
}

@Composable
private fun RttSpark(samples: List<Long>) {
    val palette = LocalHbPalette.current
    if (samples.size < 2) {
        Box(Modifier.fillMaxWidth().height(56.dp), contentAlignment = Alignment.Center) {
            HbText("AWAITING TELEMETRY_", style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
        }
        return
    }
    val max = (samples.maxOrNull() ?: 1L).coerceAtLeast(1L)
    val lineColor = palette.accentBright
    val dotColor = palette.amber
    Canvas(Modifier.fillMaxWidth().height(56.dp)) {
        val w = size.width
        val h = size.height
        listOf(0.25f, 0.5f, 0.75f).forEach { f ->
            drawLine(lineColor.copy(alpha = 0.12f), Offset(0f, h * f), Offset(w, h * f), strokeWidth = 1f)
        }
        val pts = samples.mapIndexed { i, v ->
            Offset(i / (samples.size - 1).toFloat() * w, h - 4 - (v.toFloat() / max) * (h - 10))
        }
        for (i in 0 until pts.size - 1) {
            drawLine(lineColor, pts[i], pts[i + 1], strokeWidth = 1.2.dp.toPx())
        }
        drawCircle(dotColor, radius = 2.dp.toPx(), center = pts.last())
    }
}
