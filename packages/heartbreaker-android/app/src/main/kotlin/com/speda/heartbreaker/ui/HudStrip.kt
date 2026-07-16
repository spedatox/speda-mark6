package com.speda.heartbreaker.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import com.speda.heartbreaker.data.Health
import com.speda.heartbreaker.designsystem.glass.hbSeamBottom
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType

/**
 * The fixed top telemetry strip — the mobile HudFrame variant (which the port IS,
 * per plan §0.1). M0 shows the bare essentials: link state, the centered
 * HEARTBREAKER wordmark, and the /health-derived RTT + tool count. The full DIAG
 * dropdown (host / sess / date / clock) arrives with the M3 shell.
 */
@Composable
fun HudStrip(health: Health, modifier: Modifier = Modifier) {
    val palette = LocalHbPalette.current
    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(24.dp)
            .background(palette.void.copy(alpha = 0.55f))
            .hbSeamBottom()
            .padding(horizontal = 10.dp),
    ) {
        // Left — link state.
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.align(Alignment.CenterStart)) {
            Box(
                Modifier
                    .size(6.dp)
                    .clip(CircleShape)
                    .background(if (health.online) palette.green else palette.red),
            )
            HbText(
                text = if (health.online) "ONLINE" else "OFFLINE",
                style = HbType.hud,
                color = if (health.online) palette.textDim else palette.red,
                caps = true,
                modifier = Modifier.padding(start = 6.dp),
            )
        }

        // Center — wordmark.
        HbText(
            text = "HEARTBREAKER",
            style = HbType.hud,
            color = palette.accentBright,
            caps = true,
            modifier = Modifier.align(Alignment.Center),
        )

        // Right — RTT + tools.
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            modifier = Modifier.align(Alignment.CenterEnd),
        ) {
            HbText(
                text = health.latencyMs?.let { "${it}MS" } ?: "—",
                style = HbType.hud,
                color = if ((health.latencyMs ?: Long.MAX_VALUE) < 400) palette.green else palette.textDim,
                caps = true,
            )
            HbText(
                text = health.tools?.let { "$it TOOLS" } ?: "— TOOLS",
                style = HbType.hud,
                color = palette.textDim,
                caps = true,
            )
        }
    }
}
