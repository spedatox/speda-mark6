package com.speda.heartbreaker.ui.party

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.AgentCommEntry
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.glass.hbSeamBottom
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbText
import com.speda.heartbreaker.ui.WarMode
import kotlinx.coroutines.delay

/**
 * PARTY ROSTER — the strip under the header while the war room is up.
 *
 * A port of PartyRosterStrip.tsx. It answers one question at a glance: who is
 * actually doing something right now. Each agent carries a jewel — amber and
 * pulsing while it has a live task, dim when it is standing by — plus the count
 * of tasks it has finished this session.
 *
 * The strip is the only place the war room's own controls live: CORES (the model
 * pins for the whole roster) and EXIT / STAND DOWN. STAND DOWN is deliberately
 * more prominent than EXIT: leaving standby costs nothing, dropping an engaged
 * protocol stops the meter.
 */
@Composable
fun PartyRosterStrip(
    api: IgorApi,
    config: AppConfig,
    mode: WarMode,
    onOpenCores: () -> Unit,
    onExit: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current
    var comms by remember { mutableStateOf<List<AgentCommEntry>>(emptyList()) }

    // 2.5s — the strip is a liveness readout, and a slower tick makes a
    // legionnaire that ran for three seconds look like it never ran at all.
    LaunchedEffect(config, mode) {
        while (true) {
            comms = api.fetchAgentComms(config, limit = 120)
            delay(2_500)
        }
    }

    // Who is working, and who has finished what. Counting by TO-agent: the row
    // is about the agent doing the task, not the one who asked for it.
    val working = remember(comms) { comms.filter { it.status == "running" }.map { it.toAgent }.toSet() }
    val done = remember(comms) {
        comms.filter { it.status == "ok" }.groupingBy { it.toAgent }.eachCount()
    }

    Column(
        modifier
            .fillMaxWidth()
            .hbSeamBottom()
            .padding(horizontal = 10.dp, vertical = 6.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            HbText(
                if (mode == WarMode.Engaged) "HOUSE PARTY // ENGAGED" else "WAR ROOM // STANDBY",
                style = HbType.headerBar.copy(fontSize = 10.sp),
                color = if (mode == WarMode.Engaged) palette.amberBright else palette.textDim,
                caps = true,
            )
            Spacer(Modifier.weight(1f))
            StripButton("CORES", palette.textDim, onOpenCores)
            StripButton(
                if (mode == WarMode.Engaged) "STAND DOWN" else "EXIT",
                if (mode == WarMode.Engaged) palette.amberBright else palette.textDim,
                onExit,
            )
        }

        Spacer(Modifier.size(6.dp))

        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Brands.ROSTER.forEach { id ->
                val brand = Brands.BRANDS[id] ?: return@forEach
                RosterPip(
                    label = brand.name,
                    accent = Color(android.graphics.Color.parseColor(brand.accent)),
                    working = id in working,
                    done = done[id] ?: 0,
                )
            }
        }
    }
}

@Composable
private fun RosterPip(label: String, accent: Color, working: Boolean, done: Int) {
    val palette = LocalHbPalette.current
    val t = rememberInfiniteTransition(label = "pip")
    val pulse by t.animateFloat(
        initialValue = 0.3f, targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(750, easing = LinearEasing), RepeatMode.Reverse),
        label = "pip-pulse",
    )

    Row(
        Modifier
            .hbGlass(
                shape = HbGlassShape.Pill,
                state = if (working) HbGlassState.Tint(accent) else HbGlassState.Default,
            )
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        Box(
            Modifier.size(5.dp).background(
                // The jewel is the agent's OWN colour while it works — the strip
                // has to read as a roster, not as a row of identical lights.
                if (working) accent.copy(alpha = pulse) else palette.iconDim,
                CircleShape,
            ),
        )
        HbText(
            label,
            style = HbType.label.copy(fontSize = 9.sp),
            color = if (working) accent else palette.textFaint,
            caps = true,
        )
        if (done > 0) {
            HbText("$done", style = HbType.readout.copy(fontSize = 9.sp), color = palette.textFaint)
        }
    }
}

@Composable
private fun StripButton(label: String, color: Color, onClick: () -> Unit) {
    Box(
        Modifier
            .hbGlass(shape = HbGlassShape.Pill)
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 5.dp),
    ) {
        HbText(label, style = HbType.label.copy(fontSize = 10.sp), color = color, caps = true)
    }
}
