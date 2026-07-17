package com.speda.heartbreaker.ui.prose

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.theme.ThemeEngine
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.CalDay
import com.speda.heartbreaker.domain.CalEvent
import com.speda.heartbreaker.domain.looksIncomplete
import com.speda.heartbreaker.domain.parseCalendarSpec
import java.time.LocalDate

private val WEEKDAYS = listOf("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")
private val MONTHS = listOf("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

/**
 * ```calendar fences — the Jarvis holographic calendar, a port of
 * CalendarBlock.tsx: layered glass ghosts behind a frosted panel, a concentric
 * HUD ring, and today's date as a large glowing numeral.
 *
 * Mobile: seven columns can't share a phone's width, so the day strip scrolls
 * horizontally (the web does the same with overflow-x: auto).
 */
@Composable
fun CalendarBlock(raw: String, modifier: Modifier = Modifier) {
    val spec = remember(raw) { parseCalendarSpec(raw) }
    if (spec == null) {
        if (looksIncomplete(raw)) Materializing("CALENDAR", modifier) else ParseError("CALENDAR", raw, modifier)
        return
    }
    val palette = LocalHbPalette.current
    val today = remember { LocalDate.now() }
    val first = spec.days.firstOrNull()?.let { it.localDate }
    val monthLabel = first?.let { "${MONTHS[it.monthValue - 1]} ${it.year}" }.orEmpty()

    Box(modifier.fillMaxWidth().padding(vertical = 14.dp)) {
        // Layered glass ghosts behind — the stacked depth of the reference.
        Box(
            Modifier
                .padding(start = 8.dp, top = 8.dp)
                .matchParentSize()
                .clip(RoundedCornerShape(16.dp))
                .background(Color(0xFFBED7EB).copy(alpha = 0.018f))
                .border(1.dp, palette.accent.copy(alpha = 0.10f), RoundedCornerShape(16.dp)),
        )
        Box(
            Modifier
                .padding(start = 4.dp, top = 4.dp)
                .matchParentSize()
                .clip(RoundedCornerShape(16.dp))
                .background(Color(0xFFBED7EB).copy(alpha = 0.025f))
                .border(1.dp, palette.accent.copy(alpha = 0.14f), RoundedCornerShape(16.dp)),
        )

        // Main holographic panel
        Box(Modifier.fillMaxWidth().hbGlass(shape = HbGlassShape.R14)) {
            HudRing(Modifier.align(Alignment.CenterEnd))

            Column(Modifier.padding(start = 14.dp, end = 14.dp, top = 13.dp, bottom = 14.dp)) {
                // Header
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.Top,
                ) {
                    Column(Modifier.weight(1f)) {
                        BasicText(
                            AnnotatedString(spec.title ?: "CALENDAR"),
                            style = HbType.headerBar.copy(
                                fontSize = 15.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.22.em,
                                color = Color.White,
                            ),
                            maxLines = 1,
                        )
                        if (!spec.range.isNullOrBlank()) {
                            BasicText(
                                AnnotatedString(spec.range),
                                style = HbType.readout.copy(
                                    fontSize = 10.sp, letterSpacing = 0.1.em, color = palette.textFaint,
                                ),
                                modifier = Modifier.padding(top = 2.dp),
                            )
                        }
                    }
                    if (monthLabel.isNotEmpty()) {
                        BasicText(
                            AnnotatedString(monthLabel),
                            style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.14.em, color = palette.accent),
                            maxLines = 1,
                        )
                    }
                }

                // Hairline divider
                Box(
                    Modifier
                        .fillMaxWidth()
                        .padding(top = 11.dp, bottom = 10.dp)
                        .height(1.dp)
                        .background(Brush.horizontalGradient(listOf(palette.edgeBright, Color.Transparent))),
                )

                // Day columns
                Row(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(5.dp),
                ) {
                    spec.days.forEach { day -> DayColumn(day, today) }
                }
            }
        }
    }
}

/** Faint concentric arcs — the Jarvis interface ring. */
@Composable
private fun HudRing(modifier: Modifier = Modifier) {
    val palette = LocalHbPalette.current
    Canvas(modifier.size(220.dp).padding(end = 0.dp)) {
        val c = Offset(size.width * 0.62f, size.height / 2f)
        val k = size.minDimension / 200f
        fun ring(r: Float, alpha: Float, w: Float, dash: Boolean = false) = drawCircle(
            color = palette.accent.copy(alpha = alpha),
            radius = r * k,
            center = c,
            style = Stroke(
                width = w * k,
                pathEffect = if (dash) PathEffect.dashPathEffect(floatArrayOf(2f * k, 5f * k)) else null,
            ),
        )
        ring(92f, 0.18f * 0.5f, 0.5f)
        ring(72f, 0.30f * 0.5f, 0.5f, dash = true)
        ring(50f, 0.16f * 0.5f, 0.5f)
        // Two bright quarter arcs
        drawArc(
            color = palette.accent.copy(alpha = 0.5f * 0.5f),
            startAngle = -90f, sweepAngle = 90f, useCenter = false,
            topLeft = Offset(c.x - 92f * k, c.y - 92f * k),
            size = Size(184f * k, 184f * k),
            style = Stroke(width = 1.2f * k),
        )
        drawArc(
            color = palette.accent.copy(alpha = 0.3f * 0.5f),
            startAngle = 90f, sweepAngle = 90f, useCenter = false,
            topLeft = Offset(c.x - 92f * k, c.y - 92f * k),
            size = Size(184f * k, 184f * k),
            style = Stroke(width = 1.2f * k),
        )
    }
}

@Composable
private fun DayColumn(day: CalDay, today: LocalDate) {
    val palette = LocalHbPalette.current
    val d = day.localDate
    val isToday = d == today
    val wd = day.label ?: d?.let { WEEKDAYS[it.dayOfWeek.value % 7] } ?: "—"
    val num = d?.dayOfMonth?.toString().orEmpty()
    val events = remember(day) { day.events.sortedBy { it.time ?: "" } }

    Column(
        Modifier
            .width(96.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(if (isToday) palette.accent.copy(alpha = 0.10f) else Color.Transparent)
            .border(
                1.dp,
                if (isToday) palette.edgeBright else Color.Transparent,
                RoundedCornerShape(10.dp),
            )
            .padding(horizontal = 6.dp, vertical = 9.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        BasicText(
            AnnotatedString(wd),
            style = HbType.readout.copy(
                fontSize = 9.sp, letterSpacing = 0.18.em,
                color = if (isToday) palette.accentBright else palette.textFaint,
            ),
        )
        BasicText(
            AnnotatedString(num),
            style = HbType.numThin.copy(
                fontSize = if (isToday) 34.sp else 20.sp,
                color = if (isToday) palette.accentBright else palette.textDim,
            ),
            modifier = Modifier.padding(top = 2.dp),
        )
        Box(Modifier.height(7.dp))
        if (events.isEmpty()) {
            BasicText(
                AnnotatedString("·"),
                style = HbType.readout.copy(fontSize = 11.sp, color = palette.textFaint.copy(alpha = 0.4f)),
            )
        } else {
            events.forEach { EventChip(it) }
        }
    }
}

@Composable
private fun EventChip(ev: CalEvent) {
    val palette = LocalHbPalette.current
    val accent = ev.color?.takeIf { it.startsWith("#") }
        ?.let { runCatching { ThemeEngine.parseHex(it) }.getOrNull() } ?: palette.accent
    Column(
        Modifier
            .fillMaxWidth()
            .padding(bottom = 5.dp)
            .clip(RoundedCornerShape(6.dp))
            .background(palette.accent.copy(alpha = 0.07f))
            .drawLeftRule(accent)
            .padding(start = 9.dp, end = 6.dp, top = 5.dp, bottom = 5.dp),
    ) {
        if (!ev.time.isNullOrBlank()) {
            BasicText(
                AnnotatedString(ev.time + if (!ev.end.isNullOrBlank()) "–${ev.end}" else ""),
                style = HbType.readout.copy(fontSize = 9.5.sp, letterSpacing = 0.06.em, color = palette.accentBright),
            )
        }
        BasicText(
            AnnotatedString(ev.title),
            style = HbType.headerBar.copy(
                fontSize = 12.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.01.em,
                lineHeight = 1.2.em, color = palette.text,
            ),
        )
        if (!ev.location.isNullOrBlank()) {
            BasicText(
                AnnotatedString(ev.location),
                style = HbType.readout.copy(fontSize = 8.5.sp, letterSpacing = 0.04.em, color = palette.textFaint),
                maxLines = 1,
            )
        }
    }
}

/** `border-left: 2px solid <accent>` on a rounded chip. */
private fun Modifier.drawLeftRule(color: Color) = this.drawBehind {
    drawRect(color, Offset.Zero, Size(2.dp.toPx(), size.height))
}
