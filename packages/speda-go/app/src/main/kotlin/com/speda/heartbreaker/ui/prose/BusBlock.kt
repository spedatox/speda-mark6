// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.prose

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.HbPalette
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.BusEntry
import com.speda.heartbreaker.domain.looksIncomplete
import com.speda.heartbreaker.domain.parseBusSpec
import com.speda.heartbreaker.i18n.LocalStrings

/**
 * ```bus fences — the EGO "Otobüs Nerede?" arrivals board for one stop, a
 * static glass list card (see prompts/core/06_visual_output.md). No live
 * poll after render, unlike [AircraftBlock]: the whole snapshot is stale
 * within seconds regardless, so there is nothing worth re-fetching on a
 * timer — the fence IS the answer, once.
 */
@Composable
fun BusBlock(raw: String, modifier: Modifier = Modifier) {
    val t = LocalStrings.current
    val spec = remember(raw) { parseBusSpec(raw) }
    if (spec == null) {
        if (looksIncomplete(raw)) Materializing(t.proseKind.bus, modifier) else ParseError(t.proseKind.bus, raw, modifier)
        return
    }
    val palette = LocalHbPalette.current
    val liveCount = spec.entries.count { it.live }

    Box(modifier.fillMaxWidth().padding(vertical = 12.dp).hbGlass(shape = HbGlassShape.Card)) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 13.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.Top,
            ) {
                BasicText(
                    AnnotatedString(t.bus.busStop(spec.stopNumber)),
                    style = HbType.headerBar.copy(
                        fontSize = 15.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.18.em,
                        color = Color.White,
                    ),
                    maxLines = 1,
                )
                BasicText(
                    AnnotatedString(if (liveCount > 0) t.bus.live(liveCount) else t.bus.schedule),
                    style = HbType.readout.copy(
                        fontSize = 10.sp, letterSpacing = 0.12.em,
                        color = if (liveCount > 0) palette.green else palette.textFaint,
                    ),
                )
            }

            Box(
                Modifier
                    .fillMaxWidth()
                    .padding(top = 10.dp, bottom = 8.dp)
                    .height(1.dp)
                    .background(Brush.horizontalGradient(listOf(palette.edgeBright, Color.Transparent))),
            )

            spec.entries.forEach { BusRow(it, palette) }
        }
    }
}

@Composable
private fun BusRow(e: BusEntry, palette: HbPalette) {
    val etaColor = when {
        !e.live -> palette.textFaint
        e.eta == "Geldi" -> palette.green
        e.eta == "Gidiyor" -> palette.amber
        else -> palette.accentBright
    }
    Row(
        Modifier
            .fillMaxWidth()
            .padding(bottom = 6.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(palette.accent.copy(alpha = 0.06f))
            .padding(horizontal = 10.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f).padding(end = 10.dp)) {
            BasicText(
                AnnotatedString(e.line),
                style = HbType.readout.copy(
                    fontSize = 12.5.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.04.em,
                    color = palette.accentBright,
                ),
            )
            BasicText(
                AnnotatedString(e.route),
                style = HbType.headerBar.copy(fontSize = 11.5.sp, lineHeight = 1.25.em, color = palette.text),
                maxLines = 2,
            )
            if (e.live) {
                val subParts = buildList {
                    e.plate?.let { add(it) }
                    e.speedKmh?.let { add("$it km/h") }
                    if (e.tags.isNotEmpty()) add(e.tags.joinToString(", "))
                }
                if (subParts.isNotEmpty()) {
                    BasicText(
                        AnnotatedString(subParts.joinToString(" · ")),
                        style = HbType.readout.copy(fontSize = 9.5.sp, letterSpacing = 0.03.em, color = palette.textFaint),
                        modifier = Modifier.padding(top = 2.dp),
                    )
                }
            }
        }
        Column(horizontalAlignment = Alignment.End) {
            BasicText(
                AnnotatedString(if (e.live) (e.eta ?: "—") else (e.nextDeparture ?: "—")),
                style = HbType.readout.copy(fontSize = 13.sp, fontWeight = FontWeight.Bold, color = etaColor),
            )
            if (e.live && e.stopIndex != null && e.totalStops != null) {
                BasicText(
                    AnnotatedString("stop ${e.stopIndex}/${e.totalStops}"),
                    style = HbType.readout.copy(fontSize = 9.sp, color = palette.textFaint),
                )
            } else if (!e.live && e.inWords != null) {
                BasicText(
                    AnnotatedString(e.inWords),
                    style = HbType.readout.copy(fontSize = 9.sp, color = palette.textFaint),
                )
            }
        }
    }
}
