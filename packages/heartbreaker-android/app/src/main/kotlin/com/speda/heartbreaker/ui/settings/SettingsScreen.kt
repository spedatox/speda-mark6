package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.interaction.MutableInteractionSource
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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.HbSettings
import com.speda.heartbreaker.data.ModelInfo
import com.speda.heartbreaker.designsystem.brand.Brand
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.glass.hbSeamBottom
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbText

private enum class SettingsTab(val label: String) {
    General("General"),
    Config("Configuration"),
    Connections("Connections"),
    Automations("Automations"),
    Interface("Interface"),
    Data("Data"),
    Account("Account"),
}

/**
 * Full-screen settings — the mobile port of SettingsModal.tsx. The desktop's
 * left nav becomes a horizontal tab strip; each tab is a self-contained
 * composable that loads its own slice of the backend.
 */
@Composable
fun SettingsScreen(
    config: AppConfig,
    graph: AppGraph,
    settings: HbSettings,
    models: List<ModelInfo>,
    brand: Brand,
    onResetUplink: () -> Unit,
    onClose: () -> Unit,
) {
    val palette = LocalHbPalette.current
    var tab by remember { mutableStateOf(SettingsTab.General) }

    Column(
        Modifier
            .fillMaxSize()
            .background(palette.base)
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {}
            .statusBarsPadding()
            .navigationBarsPadding(),
    ) {
        // ── Top bar ──────────────────────────────────────────────────────────
        Row(
            Modifier.fillMaxWidth().height(44.dp).padding(horizontal = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Box(
                Modifier.size(30.dp).hbGlass(shape = HbGlassShape.R9).clickable(onClick = onClose),
                contentAlignment = Alignment.Center,
            ) { HbGlyphs.Close(palette.iconBright, size = 13.dp) }
            HbText("SETTINGS", style = HbType.headerBar.copy(fontSize = 13.sp), color = Color.White, caps = true)
            Spacer(Modifier.weight(1f))
            HbText(brand.name, style = HbType.headerBar.copy(fontSize = 12.sp), color = palette.accent, caps = true, maxLines = 1)
            HbText(brand.modelNumber, style = HbType.headerBar.copy(fontSize = 10.sp), color = palette.accentDim, caps = true, maxLines = 1)
        }

        // ── Tab strip ─────────────────────────────────────────────────────────
        Row(
            Modifier
                .fillMaxWidth()
                .hbSeamBottom()
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 10.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            SettingsTab.entries.forEach { t -> TabChip(t.label, active = t == tab) { tab = t } }
        }

        // ── Content ───────────────────────────────────────────────────────────
        Box(Modifier.weight(1f).fillMaxWidth()) {
            when (tab) {
                SettingsTab.General -> GeneralTab(config, graph, settings)
                SettingsTab.Config -> ConfigTabView(config, graph)
                SettingsTab.Connections -> ConnectionsTab(config, graph)
                SettingsTab.Automations -> AutomationsTab(config, graph)
                SettingsTab.Interface -> InterfaceTab(config, graph)
                SettingsTab.Data -> DataTab(config, graph)
                SettingsTab.Account -> AccountTab(config, graph, settings, brand, onResetUplink)
            }
        }
    }
}

@Composable
private fun TabChip(label: String, active: Boolean, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Box(
        Modifier
            .hbGlass(shape = HbGlassShape.Pill, state = if (active) HbGlassState.Tint(palette.accent) else HbGlassState.Default)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
        contentAlignment = Alignment.Center,
    ) {
        HbText(
            label,
            style = HbType.headerBar.copy(fontSize = 11.sp),
            color = if (active) palette.accentBright else palette.textDim,
            caps = true,
            maxLines = 1,
        )
    }
}
