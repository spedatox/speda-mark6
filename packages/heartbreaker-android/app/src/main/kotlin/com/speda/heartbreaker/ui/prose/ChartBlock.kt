package com.speda.heartbreaker.ui.prose

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.theme.HbPalette
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.theme.ThemeEngine
import com.speda.heartbreaker.designsystem.type.HbFonts
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.ChartSpec
import com.speda.heartbreaker.domain.looksIncomplete
import com.speda.heartbreaker.domain.parseChartSpec
import java.util.Locale
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * ```chart fences — the Stark chart renderer, a port of ChartBlock.tsx. The web
 * draws with Recharts; the plan commits to a custom Canvas here because the spec
 * is only four chart types and no charting library reproduces this styling.
 *
 * Tooltips are tap-driven on touch and land with the interaction pass; the chart
 * itself renders statically.
 */
@Composable
fun ChartBlock(raw: String, modifier: Modifier = Modifier) {
    val spec = remember(raw) { parseChartSpec(raw) }
    when {
        spec != null -> ChartPanel(spec.title, modifier) { StarkChart(spec) }
        // Unbalanced JSON means it's still streaming, not malformed — a quiet
        // placeholder beats a scary error that vanishes a second later.
        looksIncomplete(raw) -> Materializing("CHART", modifier)
        else -> ParseError("CHART", raw, modifier)
    }
}

/* ── Panel shell ─────────────────────────────────────────────────────────── */

@Composable
fun ChartPanel(title: String?, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val palette = LocalHbPalette.current
    Column(
        modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(Color(0xFF060E16).copy(alpha = 0.55f))
            .border(1.dp, palette.edge, RoundedCornerShape(12.dp)),
    ) {
        if (!title.isNullOrBlank()) {
            // Panel header — frosted accent glass, MAIN_SUB split.
            val i = title.indexOf('_')
            val text = buildAnnotatedString {
                withStyle(SpanStyle(color = Color.White)) { append(if (i > -1) title.substring(0, i) else title) }
                if (i > -1) withStyle(SpanStyle(color = palette.accent)) { append(title.substring(i)) }
            }
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(28.dp)
                    .background(palette.accent.copy(alpha = 0.10f))
                    .padding(horizontal = 12.dp),
                contentAlignment = Alignment.CenterStart,
            ) {
                BasicText(
                    text = text,
                    style = HbType.headerBar.copy(fontSize = 13.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.2.em),
                )
            }
            Box(Modifier.fillMaxWidth().height(1.dp).background(palette.accent.copy(alpha = 0.22f)))
        }
        Box(Modifier.padding(start = 0.dp, top = 12.dp, end = 4.dp, bottom = 8.dp)) { content() }
    }
}

/** The quiet placeholder while the fence's JSON is still streaming. */
@Composable
fun Materializing(kind: String, modifier: Modifier = Modifier) {
    val palette = LocalHbPalette.current
    ChartPanel(title = null, modifier = modifier) {
        Row(
            Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(9.dp),
        ) {
            val t = rememberInfiniteTransition(label = "materializing")
            val a by t.animateFloat(
                initialValue = 0.3f,
                targetValue = 0.6f,
                animationSpec = infiniteRepeatable(tween(1400), RepeatMode.Reverse),
                label = "skeletonPulse",
            )
            Box(Modifier.size(6.dp).clip(CircleShape).background(palette.accentBright.copy(alpha = a)))
            BasicText(
                AnnotatedString("$kind // MATERIALIZING"),
                style = HbType.readout.copy(fontSize = 11.sp, letterSpacing = 0.14.em, color = palette.textFaint),
            )
        }
    }
}

@Composable
fun ParseError(kind: String, raw: String, modifier: Modifier = Modifier) {
    Column(
        modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp)
            .background(Color(0xFFC84A3A).copy(alpha = 0.09f))
            .border(1.dp, Color(0xFFC84A3A).copy(alpha = 0.35f))
            .padding(horizontal = 12.dp, vertical = 8.dp),
    ) {
        BasicText(
            AnnotatedString("$kind // PARSE ERROR"),
            style = HbType.readout.copy(fontSize = 11.5.sp, letterSpacing = 0.05.em, color = Color(0xFFC84A3A)),
        )
        BasicText(
            AnnotatedString(raw.take(120)),
            style = HbType.readout.copy(fontSize = 10.sp, color = LocalHbPalette.current.textFaint),
        )
    }
}

/* ── The charts ──────────────────────────────────────────────────────────── */

@OptIn(ExperimentalTextApi::class)
@Composable
private fun StarkChart(spec: ChartSpec) {
    val palette = LocalHbPalette.current
    val measurer = rememberTextMeasurer()
    val height = (spec.height ?: if (spec.type == "pie") 230 else 210).dp

    // A Column, not the panel's Box — otherwise the legend stacks ON the canvas.
    Column(Modifier.fillMaxWidth()) {
        Canvas(Modifier.fillMaxWidth().height(height)) {
            when (spec.type) {
                "pie" -> drawPie(spec, palette, measurer)
                else -> drawCartesian(spec, palette, measurer)
            }
        }

        // Legend — only with 2+ series (the web hides it for a single series).
        val series = spec.series
        if (series.size >= 2) {
            Row(
                Modifier.fillMaxWidth().padding(top = 6.dp),
                horizontalArrangement = Arrangement.Center,
            ) {
                series.forEachIndexed { i, s ->
                    Row(
                        Modifier.padding(horizontal = 9.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Box(
                            Modifier
                                .size(width = 16.dp, height = 1.5.dp)
                                .background(resolveSeriesColor(s.color, i, palette)),
                        )
                        BasicText(
                            AnnotatedString((s.label ?: s.key).uppercase(Locale.ENGLISH)),
                            style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.1.em, color = palette.textFaint),
                        )
                    }
                }
            }
        }
    }
}

private val TICK_SIZE = 10.sp

@OptIn(ExperimentalTextApi::class)
private fun DrawScope.drawCartesian(spec: ChartSpec, palette: HbPalette, measurer: TextMeasurer) {
    val grid = palette.accent.copy(alpha = 0.10f)
    val axis = palette.accent.copy(alpha = 0.30f)
    val tickStyle = TextStyle(fontFamily = HbFonts.Read, fontSize = TICK_SIZE, color = palette.textFaint)

    val left = 44.dp.toPx()   // yAxisProps.width
    val right = 16.dp.toPx()
    val top = 6.dp.toPx()
    val bottom = 20.dp.toPx()
    val plot = Rect(left, top, size.width - right, size.height - bottom)
    if (plot.width <= 0 || plot.height <= 0) return

    // Y domain — from the spec, else from the data (bars start at 0).
    val values = spec.data.flatMap { row -> spec.series.mapNotNull { (row[it.key] as? Number)?.toFloat() } }
    if (values.isEmpty()) return
    val autoMin = if (spec.type == "bar") 0f else values.min()
    val yMin = spec.yDomain?.getOrNull(0) ?: autoMin
    val yMax = spec.yDomain?.getOrNull(1) ?: values.max()
    val span = (yMax - yMin).takeIf { it > 0f } ?: 1f

    fun yOf(v: Float) = plot.bottom - (v - yMin) / span * plot.height

    // Horizontal grid + Y ticks (CartesianGrid vertical={false}).
    val steps = 4
    for (i in 0..steps) {
        val v = yMin + span * i / steps
        val y = yOf(v)
        drawLine(grid, Offset(plot.left, y), Offset(plot.right, y), 1f)
        val label = measurer.measure(AnnotatedString(formatTick(v)), tickStyle)
        drawText(label, topLeft = Offset(plot.left - label.size.width - 6.dp.toPx(), y - label.size.height / 2f))
    }

    // X axis line + category labels.
    drawLine(axis, Offset(plot.left, plot.bottom), Offset(plot.right, plot.bottom), 1f)
    val n = spec.data.size
    if (n == 0) return
    fun xOf(i: Int): Float =
        if (spec.type == "bar" || n == 1) plot.left + plot.width * (i + 0.5f) / n
        else plot.left + plot.width * i / (n - 1).coerceAtLeast(1)

    spec.data.forEachIndexed { i, row ->
        val name = row[spec.xKey]?.toString() ?: return@forEachIndexed
        val l = measurer.measure(AnnotatedString(name), tickStyle)
        drawText(l, topLeft = Offset(xOf(i) - l.size.width / 2f, plot.bottom + 4.dp.toPx()))
        drawLine(axis, Offset(xOf(i), plot.bottom), Offset(xOf(i), plot.bottom + 3.dp.toPx()), 1f)
    }

    when (spec.type) {
        "bar" -> {
            val groupW = plot.width / n
            val barW = groupW * 0.68f / spec.series.size // barCategoryGap 32%
            spec.series.forEachIndexed { si, s ->
                val c = resolveSeriesColor(s.color, si, palette)
                spec.data.forEachIndexed { i, row ->
                    val v = (row[s.key] as? Number)?.toFloat() ?: return@forEachIndexed
                    val cx = plot.left + groupW * (i + 0.5f)
                    val x = cx - (barW * spec.series.size) / 2f + si * barW
                    val y = yOf(v)
                    drawRect(c.copy(alpha = 0.52f), Offset(x, y), Size(barW, plot.bottom - y))
                    drawRect(c, Offset(x, y), Size(barW, plot.bottom - y), style = Stroke(1f))
                }
            }
        }
        else -> spec.series.forEachIndexed { si, s ->
            val c = resolveSeriesColor(s.color, si, palette)
            val pts = spec.data.mapIndexedNotNull { i, row ->
                (row[s.key] as? Number)?.toFloat()?.let { Offset(xOf(i), yOf(it)) }
            }
            if (pts.isEmpty()) return@forEachIndexed
            val path = monotonePath(pts)
            if (spec.type == "area") {
                val fill = Path().apply {
                    addPath(path)
                    lineTo(pts.last().x, plot.bottom)
                    lineTo(pts.first().x, plot.bottom)
                    close()
                }
                drawPath(
                    fill,
                    Brush.verticalGradient(
                        0.05f to c.copy(alpha = 0.35f),
                        0.95f to c.copy(alpha = 0.02f),
                        startY = plot.top,
                        endY = plot.bottom,
                    ),
                )
            }
            drawPath(path, c, style = Stroke(width = 1.6.dp.toPx()))
            val r = if (spec.type == "area") 2.5.dp.toPx() else 3.dp.toPx()
            pts.forEach { drawCircle(c, r, it) }
        }
    }
}

/** Catmull-Rom → bezier, approximating Recharts' `type="monotone"`. */
private fun monotonePath(pts: List<Offset>): Path {
    val p = Path()
    if (pts.isEmpty()) return p
    p.moveTo(pts[0].x, pts[0].y)
    if (pts.size == 1) return p
    for (i in 0 until pts.size - 1) {
        val p0 = pts[max(0, i - 1)]
        val p1 = pts[i]
        val p2 = pts[i + 1]
        val p3 = pts[min(pts.size - 1, i + 2)]
        val c1 = Offset(p1.x + (p2.x - p0.x) / 6f, p1.y + (p2.y - p0.y) / 6f)
        val c2 = Offset(p2.x - (p3.x - p1.x) / 6f, p2.y - (p3.y - p1.y) / 6f)
        p.cubicTo(c1.x, c1.y, c2.x, c2.y, p2.x, p2.y)
    }
    return p
}

@OptIn(ExperimentalTextApi::class)
private fun DrawScope.drawPie(spec: ChartSpec, palette: HbPalette, measurer: TextMeasurer) {
    val total = spec.data.sumOf { ((it["value"] as? Number)?.toDouble() ?: 0.0) }.toFloat()
    if (total <= 0f) return
    val cx = size.width / 2f
    val cy = size.height / 2f
    val outer = 75.dp.toPx()
    val inner = 38.dp.toPx()
    val labelStyle = TextStyle(fontFamily = HbFonts.Read, fontSize = TICK_SIZE, color = palette.textDim)

    var start = -90f // Recharts starts at 12 o'clock
    spec.data.forEachIndexed { i, row ->
        val v = (row["value"] as? Number)?.toFloat() ?: return@forEachIndexed
        val sweep = v / total * 360f
        val c = resolveSeriesColor(row["color"]?.toString(), i, palette)
        drawArc(
            color = c.copy(alpha = 0.82f),
            startAngle = start,
            sweepAngle = sweep,
            useCenter = false,
            topLeft = Offset(cx - (outer + inner) / 2f, cy - (outer + inner) / 2f),
            size = Size(outer + inner, outer + inner),
            style = Stroke(width = outer - inner),
        )
        // Label outside the ring: "NAME 40%"
        val mid = Math.toRadians((start + sweep / 2f).toDouble())
        val lr = outer + 22.dp.toPx()
        val lx = cx + lr * cos(mid).toFloat()
        val ly = cy + lr * sin(mid).toFloat()
        val name = row["label"]?.toString().orEmpty().uppercase(Locale.ENGLISH)
        val pct = (v / total * 100f).toInt()
        val l = measurer.measure(AnnotatedString("$name $pct%"), labelStyle)
        val lxAdj = if (lx > cx) lx else lx - l.size.width
        drawText(l, topLeft = Offset(lxAdj, ly - l.size.height / 2f))
        start += sweep
    }
}

private fun formatTick(v: Float): String =
    if (abs(v - v.toInt()) < 0.01f) v.toInt().toString() else String.format(Locale.ENGLISH, "%.1f", v)

/**
 * Series colours may arrive as a hex, or as one of the CSS custom properties the
 * web spec uses (`var(--hb-cyan)`), or be absent — in which case the shared
 * palette cycles, exactly as ChartBlock.tsx does.
 */
internal fun resolveSeriesColor(spec: String?, index: Int, palette: HbPalette): Color {
    val s = spec?.trim()
    if (s.isNullOrEmpty()) return chartPalette(palette)[index % chartPalette(palette).size]
    return when {
        s.startsWith("#") -> runCatching { ThemeEngine.parseHex(s) }.getOrNull()
            ?: chartPalette(palette)[index % 7]
        s.contains("cyan-bright") -> palette.accentBright
        s.contains("cyan-dim") -> palette.accentDim
        s.contains("cyan") || s.contains("accent") -> palette.accent
        s.contains("text-dim") -> palette.textDim
        s.contains("amber") -> palette.amber
        s.contains("green") -> palette.green
        s.contains("red") -> palette.red
        else -> chartPalette(palette)[index % 7]
    }
}

/** ChartBlock.tsx PALETTE. */
private fun chartPalette(p: HbPalette): List<Color> = listOf(
    p.accent,
    Color(0xFFD39A3A),
    Color(0xFF4FA377),
    p.accentBright,
    Color(0xFFC84A3A),
    Color(0xFF9B72CF),
    p.textDim,
)
