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
import com.speda.heartbreaker.data.LegionModelInfo
import com.speda.heartbreaker.data.ModelInfo
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import com.speda.heartbreaker.ui.settings.HbToggle
import com.speda.heartbreaker.ui.settings.Panel
import com.speda.heartbreaker.ui.settings.StatusDot
import java.util.Locale

/**
 * CORE ROUTING_MATRIX — mobile port of SystemsBoard.tsx's periodic-table model
 * tiles + MCP toolset tiles + per-agent model pins. The grid-of-tiles layout
 * doesn't reflow to a phone column, so it becomes stacked lists: tap a model to
 * route the active session onto it, tap a toolset to toggle it live, tap an
 * agent or legionnaire row to pick its pinned core inline.
 *
 * Every list here is COLLAPSED by default. Providers are queried live now, so
 * the model roster runs to dozens of rows and rendering it open buried the rest
 * of the board — DATA_BANKS sat several screens below the fold. Collapsed, each
 * section states its current selection in one line and opens on demand.
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
    legionInfos: List<LegionModelInfo>,
    onPinLegionModel: (String, String?) -> Unit,
) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    Panel {
        val activeName = models.firstOrNull { it.id == activeModel }?.name
            ?.ifEmpty { activeModel } ?: activeModel.ifEmpty { t.routingMatrix.profileDefault }
        Section(
            title = t.routingMatrix.cores(models.size),
            summary = activeName,
            color = palette.accent,
        ) {
            if (models.isEmpty()) {
                HbText(t.routingMatrix.notFound, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
            } else {
                val providers = models.map { it.provider ?: "anthropic" }.distinct()
                providers.forEach { p ->
                    val group = models.filter { (it.provider ?: "anthropic") == p }
                    // The provider holding the active model opens with the
                    // section; the rest stay shut so a 40-model NVIDIA listing
                    // never buries the one the owner came for.
                    ProviderGroup(
                        provider = p,
                        count = group.size,
                        initiallyOpen = group.any { it.id == activeModel },
                    ) {
                        group.forEach { m ->
                            ModelRow(m, active = m.id == activeModel) { onSelectModel(m.id) }
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(10.dp))
        Section(
            title = t.routingMatrix.contextShards,
            summary = t.routingMatrix.linkedCount(servers.servers.count { it.active && it.connected }, servers.servers.size),
            color = palette.amber,
        ) {
            if (servers.servers.isEmpty()) {
                HbText(t.routingMatrix.noShards, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
            } else {
                servers.servers.forEach { c -> ServerToggleRow(c) { onToggleServer(c) } }
            }
        }

        if (agentInfos.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Section(
                title = t.routingMatrix.agentCores,
                summary = agentInfos.count { it.override != null }.let { n ->
                    if (n == 0) t.routingMatrix.allOnProfile else t.routingMatrix.pinned(n)
                },
                color = palette.accentBright,
            ) {
                agentInfos.forEach { info ->
                    AgentCoreRow(info, models) { model -> onPinAgentModel(info.agentId, model) }
                }
            }
        }

        if (legionInfos.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            val pinned = legionInfos.count { it.override != null }
            val deploymentPin = legionInfos.firstNotNullOfOrNull { it.deploymentPin }
            Section(
                title = t.routingMatrix.legionCores,
                summary = when {
                    deploymentPin != null -> t.routingMatrix.envPin(deploymentPin)
                    pinned == 0 -> t.routingMatrix.allOnEffortPolicy
                    else -> t.routingMatrix.pinned(pinned)
                },
                color = palette.amber,
            ) {
                if (deploymentPin != null) {
                    HbText(
                        t.routingMatrix.deploymentPinNotice(deploymentPin),
                        style = HbType.readout.copy(fontSize = 9.sp),
                        color = palette.amber,
                        modifier = Modifier.padding(bottom = 6.dp),
                    )
                }
                legionInfos.forEach { info ->
                    LegionCoreRow(info, models) { model -> onPinLegionModel(info.workerId, model) }
                }
            }
        }
    }
}

/** A collapsed-by-default block: caption + one-line summary of what's currently
 *  selected, opening to the full list on tap. */
@Composable
private fun Section(
    title: String,
    summary: String,
    color: Color,
    content: @Composable () -> Unit,
) {
    val palette = LocalHbPalette.current
    var open by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().clickable { open = !open }.padding(vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Column(Modifier.weight(1f)) {
                HbText(title, style = HbType.readout.copy(fontSize = 9.sp), color = color, caps = true, maxLines = 1)
                HbText(summary, style = HbType.readout.copy(fontSize = 10.5.sp), color = palette.textDim, caps = true, maxLines = 1)
            }
            if (open) HbGlyphs.ChevronUp(palette.icon, size = 9.dp) else HbGlyphs.ChevronDown(palette.icon, size = 9.dp)
        }
        if (open) Column(Modifier.fillMaxWidth().padding(top = 2.dp)) { content() }
    }
}

/** One provider's models inside the CORES section — collapsible in its own
 *  right, because a single live provider listing can be dozens of rows. */
@Composable
private fun ProviderGroup(
    provider: String,
    count: Int,
    initiallyOpen: Boolean,
    content: @Composable () -> Unit,
) {
    val palette = LocalHbPalette.current
    var open by remember(provider, initiallyOpen) { mutableStateOf(initiallyOpen) }
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().clickable { open = !open }.padding(top = 6.dp, bottom = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            HbText(provider.uppercase(Locale.ENGLISH), style = HbType.readout.copy(fontSize = 9.sp), color = palette.accentBright, maxLines = 1)
            HbText("· $count", style = HbType.readout.copy(fontSize = 9.sp), color = palette.textFaint)
            Spacer(Modifier.weight(1f))
            if (open) HbGlyphs.ChevronUp(palette.icon, size = 8.dp) else HbGlyphs.ChevronDown(palette.icon, size = 8.dp)
        }
        if (open) content()
    }
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
    val t = LocalStrings.current
    var expanded by remember { mutableStateOf(false) }
    val color = hexColor(Brands.agentColor(info.agentId))
    val pinned = info.override != null
    val profileLabel = t.routingMatrix.profileLabel(info.defaultMain.substringAfterLast(':'))

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
                    if (pinned) t.routingMatrix.pinnedTo(info.override.orEmpty()) else profileLabel,
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
private fun LegionCoreRow(info: LegionModelInfo, models: List<ModelInfo>, onPin: (String?) -> Unit) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    var expanded by remember { mutableStateOf(false) }
    val pinned = info.override != null
    // A legionnaire has no model of its own to fall back to — its default is a
    // RULE resolved against whichever agent deployed it, so name the rule.
    val policyLabel = t.routingMatrix.effortLabel(info.effort.uppercase(Locale.ENGLISH), info.derivedFrom.uppercase(Locale.ENGLISH))

    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(
            Modifier.fillMaxWidth().clickable { expanded = !expanded },
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Box(
                Modifier.size(24.dp).clip(CircleShape).background(palette.amber.copy(alpha = 0.12f)),
                contentAlignment = Alignment.Center,
            ) {
                HbText(info.workerId.take(2).uppercase(Locale.ENGLISH), style = HbType.readout.copy(fontSize = 9.sp), color = palette.amber)
            }
            Column(Modifier.weight(1f)) {
                HbText(info.workerId.uppercase(Locale.ENGLISH), style = HbType.readout.copy(fontSize = 11.sp), color = palette.amber, maxLines = 1)
                HbText(
                    if (pinned) t.routingMatrix.pinnedTo(info.override.orEmpty()) else policyLabel,
                    style = HbType.readout.copy(fontSize = 9.sp),
                    color = if (pinned) palette.amber else palette.textFaint,
                    maxLines = 1,
                )
            }
            if (expanded) HbGlyphs.ChevronUp(palette.icon, size = 9.dp) else HbGlyphs.ChevronDown(palette.icon, size = 9.dp)
        }
        if (expanded) {
            Column(Modifier.fillMaxWidth().padding(start = 34.dp, top = 4.dp)) {
                HbText(info.whenToUse, style = HbType.readout.copy(fontSize = 9.sp), color = palette.textFaint, modifier = Modifier.padding(bottom = 4.dp))
                OptionRow(policyLabel, selected = !pinned) { onPin(null); expanded = false }
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
