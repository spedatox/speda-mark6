package com.speda.heartbreaker.ui.systems

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.AgentModelInfo
import com.speda.heartbreaker.data.ConnectionInfo
import com.speda.heartbreaker.data.ConnectionsResult
import com.speda.heartbreaker.data.ModelInfo
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.ui.HbText
import com.speda.heartbreaker.ui.settings.HbToggle
import com.speda.heartbreaker.ui.settings.Panel
import com.speda.heartbreaker.ui.settings.StatusDot
import java.util.Locale

/**
 * CORE ROUTING_MATRIX — mobile port of SystemsBoard.tsx's periodic-table model
 * tiles + MCP toolset tiles + per-agent model pins. The grid-of-tiles layout
 * doesn't reflow to a phone column, so it becomes three stacked lists: tap a
 * model to route the active session onto it, tap a toolset to toggle it live,
 * tap an agent row to pick its pinned core inline. Same data, same actions.
 */
@Composable
fun RoutingMatrix(
    models: List<ModelInfo>,
    servers: ConnectionsResult,
    activeModel: String,
    onSelectModel: (String) -> Unit,
    onToggleServer: (ConnectionInfo) -> Unit,
    agentInfos: List<AgentModelInfo>,
    onPinAgentModel: (String, String?) -> Unit,
) {
    val palette = LocalHbPalette.current
    Panel {
        Caption("CORES · ${models.size} MODELS", palette.accent)
        if (models.isEmpty()) {
            HbText("NOT FOUND", style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
        } else {
            val providers = models.map { it.provider ?: "anthropic" }.distinct()
            providers.forEach { p ->
                HbText(p.uppercase(Locale.ENGLISH), style = HbType.readout.copy(fontSize = 9.sp), color = palette.accentBright, modifier = Modifier.padding(top = 4.dp, bottom = 2.dp))
                models.filter { (it.provider ?: "anthropic") == p }.forEach { m ->
                    ModelRow(m, active = m.id == activeModel) { onSelectModel(m.id) }
                }
            }
        }

        Spacer(Modifier.height(10.dp))
        Caption("CONTEXT SHARDS · MCP TOOLSETS", palette.amber)
        if (servers.servers.isEmpty()) {
            HbText("NO SHARDS", style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
        } else {
            servers.servers.forEach { c -> ServerToggleRow(c) { onToggleServer(c) } }
        }

        if (agentInfos.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Caption("AGENT CORES · PER-AGENT MODEL ROUTING", palette.accentBright)
            agentInfos.forEach { info ->
                AgentCoreRow(info, models) { model -> onPinAgentModel(info.agentId, model) }
            }
        }
    }
}

@Composable
private fun Caption(text: String, color: Color) {
    HbText(text, style = HbType.readout.copy(fontSize = 9.sp), color = color, caps = true, modifier = Modifier.padding(bottom = 4.dp))
}

@Composable
private fun ModelRow(m: ModelInfo, active: Boolean, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Box(
            Modifier.size(7.dp).clip(CircleShape).background(if (active) palette.amber else palette.accent.copy(alpha = 0.18f)),
        )
        Column(Modifier.weight(1f)) {
            HbText(m.name.ifEmpty { m.id }, style = HbType.readout.copy(fontSize = 12.sp), color = if (active) Color.White else palette.textDim, caps = true, maxLines = 1)
            if (m.description.isNotEmpty()) {
                HbText(m.description, style = HbType.readout.copy(fontSize = 9.5.sp), color = palette.textFaint, maxLines = 1)
            }
        }
        if (active) HbGlyphs.Check(palette.amber, size = 12.dp)
    }
}

@Composable
private fun ServerToggleRow(c: ConnectionInfo, onToggle: () -> Unit) {
    val palette = LocalHbPalette.current
    Row(
        Modifier.fillMaxWidth().padding(vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        StatusDot(ok = c.connected, warnColor = palette.red)
        Column(Modifier.weight(1f)) {
            HbText(c.label.ifEmpty { c.server }, style = HbType.readout.copy(fontSize = 12.sp), color = palette.text, maxLines = 1)
            HbText(
                if (!c.connected) "offline" else "${c.tools} tools · ${if (c.active) "linked" else "standby"}",
                style = HbType.readout.copy(fontSize = 9.5.sp),
                color = palette.textFaint,
            )
        }
        HbToggle(checked = c.active && c.connected, enabled = c.connected, color = palette.accent, onToggle = { onToggle() })
    }
}

@Composable
private fun AgentCoreRow(info: AgentModelInfo, models: List<ModelInfo>, onPin: (String?) -> Unit) {
    val palette = LocalHbPalette.current
    var expanded by remember { mutableStateOf(false) }
    val color = hexColor(Brands.agentColor(info.agentId))
    val pinned = info.override != null
    val profileLabel = "PROFILE (${info.defaultMain.substringAfterLast(':')})"

    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(
            Modifier.fillMaxWidth().clickable { expanded = !expanded },
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Box(
                Modifier.size(24.dp).clip(CircleShape).background(color.copy(alpha = 0.12f)),
                contentAlignment = Alignment.Center,
            ) {
                HbText(Brands.monogram(info.agentId), style = HbType.readout.copy(fontSize = 9.sp), color = color)
            }
            Column(Modifier.weight(1f)) {
                HbText(info.agentId.uppercase(Locale.ENGLISH), style = HbType.readout.copy(fontSize = 11.sp), color = color, maxLines = 1)
                HbText(
                    if (pinned) "PINNED → ${info.override}" else profileLabel,
                    style = HbType.readout.copy(fontSize = 9.sp),
                    color = if (pinned) palette.amber else palette.textFaint,
                    maxLines = 1,
                )
            }
            if (expanded) HbGlyphs.ChevronUp(palette.icon, size = 9.dp) else HbGlyphs.ChevronDown(palette.icon, size = 9.dp)
        }
        if (expanded) {
            Column(Modifier.fillMaxWidth().padding(start = 34.dp, top = 4.dp)) {
                OptionRow(profileLabel, selected = !pinned) { onPin(null); expanded = false }
                models.forEach { m ->
                    OptionRow(m.name.ifEmpty { m.id }.uppercase(Locale.ENGLISH), selected = info.override == m.id) {
                        onPin(m.id); expanded = false
                    }
                }
            }
        }
    }
}

@Composable
private fun OptionRow(label: String, selected: Boolean, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        HbText(label, style = HbType.readout.copy(fontSize = 10.5.sp), color = if (selected) palette.amber else palette.textDim, maxLines = 1)
        if (selected) HbGlyphs.Check(palette.amber, size = 10.dp)
    }
}

private fun hexColor(hex: String): Color =
    runCatching { Color(android.graphics.Color.parseColor(hex)) }.getOrDefault(Color(0xFF5D7F8A))
