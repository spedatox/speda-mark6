package com.speda.heartbreaker.ui.shell

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.glass.hbHazeBlur
import com.speda.heartbreaker.designsystem.glass.hbSeamBottom
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.ui.HbText
import dev.chrisbanes.haze.HazeState

/**
 * The 40dp frosted header plate with a bottom seam — a port of Header.tsx at the
 * mobile breakpoint. Everything tagged `hb-hide-sm` in the web (MONITOR No.1, the
 * magnifier, MSGS, PROCESSING/STANDBY, FORGE LINK) is correctly absent here; what
 * remains is the panel toggle, the `:TITLE` query box and the WAR ROOM / COMMS /
 * SYS controls.
 */
@Composable
fun AppHeader(
    haze: HazeState,
    sessionTitle: String?,
    onToggleSidebar: () -> Unit,
    onOpenWarRoom: () -> Unit,
    onToggleComms: () -> Unit,
    onToggleBoard: () -> Unit,
    commsOpen: Boolean = false,
    boardOpen: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current
    Row(
        modifier = modifier
            .fillMaxWidth()
            .height(40.dp)
            .hbHazeBlur(haze, palette)
            .hbSeamBottom()
            .padding(horizontal = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        // Panel toggle
        Box(
            Modifier
                .size(width = 30.dp, height = 26.dp)
                .hbGlass(shape = HbGlassShape.R12)
                .clickable(onClick = onToggleSidebar),
            contentAlignment = Alignment.Center,
        ) { HbGlyphs.Menu(palette.iconBright) }

        // Active session title — the ":ANTON VANKO" query box
        QueryBox(title = sessionTitle?.ifBlank { null } ?: "NEW LINK")

        Spacer(Modifier.weight(1f))

        HeaderBtn("WAR ROOM", onOpenWarRoom) { HbGlyphs.WarRoom(it) }
        HeaderBtn("COMMS", onToggleComms, active = commsOpen) { HbGlyphs.Comms(it) }
        HeaderBtn("SYS", onToggleBoard, active = boardOpen) { HbGlyphs.Sys(it) }
    }
}

/** `.hb-query-box` — glass pill, a cyan ':' prefix, caps Rajdhani title. */
@Composable
private fun QueryBox(title: String, modifier: Modifier = Modifier) {
    val palette = LocalHbPalette.current
    Row(
        modifier = modifier
            .heightIn(min = 22.dp)
            .widthIn(max = 190.dp)
            .hbGlass(shape = HbGlassShape.Pill)
            .padding(start = 8.dp, end = 10.dp, top = 2.dp, bottom = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        HbText(":", style = HbType.headerBar.copy(fontSize = 12.sp, fontWeight = FontWeight.Bold), color = palette.accentBright)
        HbText(
            title,
            style = HbType.headerBar.copy(fontSize = 12.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.14.em),
            color = Color(0xFFEAF6FA),
            caps = true,
            maxLines = 1,
        )
    }
}

@Composable
private fun HeaderBtn(
    label: String,
    onClick: () -> Unit,
    active: Boolean = false,
    glyph: @Composable (Color) -> Unit,
) {
    val palette = LocalHbPalette.current
    val tint = if (active) palette.amberBright else palette.iconBright
    Row(
        modifier = Modifier
            .height(24.dp)
            .hbGlass(shape = HbGlassShape.R12, state = if (active) HbGlassState.Tint(palette.amberBright) else HbGlassState.Default)
            .clickable(onClick = onClick)
            .padding(horizontal = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        glyph(tint)
        HbText(
            label,
            style = HbType.headerBar.copy(fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.16.em),
            color = tint,
            caps = true,
            maxLines = 1,
        )
    }
}
