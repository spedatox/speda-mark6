package com.speda.heartbreaker.ui.gallery

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.glass.hbHazeBlur
import com.speda.heartbreaker.designsystem.glass.hbSeamBottom
import com.speda.heartbreaker.designsystem.glass.hbSeamTop
import com.speda.heartbreaker.designsystem.theme.HbPalette
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.ui.HbGlassButton
import com.speda.heartbreaker.ui.HbText
import dev.chrisbanes.haze.HazeState

/**
 * The design-system reference surface (plan M0 "token-gallery debug screen").
 * It is the acceptance surface for the visual-diff ritual (§7): every colour,
 * glass state, seam, and type-ramp entry side-by-side with the web gallery.
 * The agent chips drive the live palette morph; HOUSE PARTY runs the parade.
 */
@Composable
fun TokenGalleryScreen(
    modifier: Modifier = Modifier,
    haze: HazeState,
    agentId: String,
    partyEngaged: Boolean,
    onAgentChange: (String) -> Unit,
    onPartyToggle: () -> Unit,
    onResetUplink: () -> Unit,
) {
    val palette = LocalHbPalette.current
    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp),
    ) {
        // ── Controls ──────────────────────────────────────────────────────
        SectionLabel("THEME ENGINE")
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            for ((id, brand) in Brands.BRANDS) {
                val selected = id == agentId && !partyEngaged
                HbGlassButton(
                    label = brand.name,
                    onClick = { onAgentChange(id) },
                    state = if (selected) HbGlassState.Active else HbGlassState.Default,
                    shape = HbGlassShape.Pill,
                    contentColor = if (selected) palette.accentBright else palette.iconBright,
                )
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            HbGlassButton(
                label = if (partyEngaged) "STAND DOWN" else "HOUSE PARTY",
                onClick = onPartyToggle,
                state = if (partyEngaged) HbGlassState.Amber else HbGlassState.Default,
                shape = HbGlassShape.Pill,
                contentColor = if (partyEngaged) palette.amberBright else palette.iconBright,
            )
            HbGlassButton(
                label = "RESET UPLINK",
                onClick = onResetUplink,
                shape = HbGlassShape.Pill,
                contentColor = palette.textFaint,
            )
        }

        // ── Palette swatches ──────────────────────────────────────────────
        SectionLabel("PALETTE — ${agentId.uppercase()}")
        SwatchGrid(palette)

        // ── Glass states (top-level over the void → real backdrop blur) ────
        SectionLabel("GLASS MATERIAL")
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            GlassTile("DEFAULT", haze, HbGlassState.Default)
            GlassTile("ACTIVE", haze, HbGlassState.Active)
            GlassTile("AMBER", haze, HbGlassState.Amber)
            GlassTile("TINT", haze, HbGlassState.Tint(palette.accent))
        }

        // ── Seams ─────────────────────────────────────────────────────────
        SectionLabel("ETCHED SEAMS")
        Box(
            Modifier
                .fillMaxWidth()
                .height(56.dp)
                .background(palette.petrol.copy(alpha = 0.4f))
                .hbSeamTop()
                .hbSeamBottom(),
            contentAlignment = Alignment.Center,
        ) {
            HbText("groove + light-catch, dissolving toward the ends", style = HbType.label, color = palette.textDim)
        }

        // ── Type ramp ─────────────────────────────────────────────────────
        SectionLabel("TYPE RAMP")
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            HbText("HB-LABEL · 0.18EM CAPS", style = HbType.label, color = palette.textDim, caps = true)
            HbText("HEADER BAR — RAJDHANI 700", style = HbType.headerBar, color = palette.text, caps = true)
            HbText("Inter body copy — the quick petrol fox drifts behind glass.", style = HbType.read, color = palette.text)
            HbText("readout 0.04em", style = HbType.readout, color = palette.accentBright)
            HbText("val x = 0xDEADBEEF // mono", style = HbType.code, color = palette.text)
        }

        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun SectionLabel(text: String) {
    HbText(text, style = HbType.label, color = LocalHbPalette.current.accentBright, caps = true)
}

@Composable
private fun SwatchGrid(palette: HbPalette) {
    val swatches = listOf(
        "void" to palette.void, "base" to palette.base, "petrol" to palette.petrol, "steel" to palette.steel,
        "text" to palette.text, "text-dim" to palette.textDim, "text-faint" to palette.textFaint,
        "icon" to palette.icon, "icon-dim" to palette.iconDim, "icon-bright" to palette.iconBright,
        "accent" to palette.accent, "bright" to palette.accentBright, "dim" to palette.accentDim,
        "line" to palette.line, "edge" to palette.edge, "edge-bright" to palette.edgeBright,
        "amber" to palette.amber, "amber-br" to palette.amberBright, "red" to palette.red, "green" to palette.green,
    )
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        swatches.chunked(4).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { (label, color) -> Swatch(label, color, palette) }
            }
        }
    }
}

@Composable
private fun Swatch(label: String, color: Color, palette: HbPalette) {
    Column(
        modifier = Modifier.width(76.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            Modifier
                .size(width = 76.dp, height = 40.dp)
                .background(palette.void) // so alpha tokens read against the void
                .background(color)
                .border(1.dp, palette.edge),
        )
        Spacer(Modifier.height(4.dp))
        HbText(label, style = HbType.hud, color = palette.textDim, maxLines = 1)
    }
}

@Composable
private fun GlassTile(label: String, haze: HazeState, state: HbGlassState) {
    val palette = LocalHbPalette.current
    Box(
        modifier = Modifier
            .size(width = 120.dp, height = 72.dp)
            .hbHazeBlur(haze, palette)
            .hbGlass(shape = HbGlassShape.R14, state = state),
        contentAlignment = Alignment.Center,
    ) {
        HbText(label, style = HbType.label, color = palette.text, caps = true)
    }
}
