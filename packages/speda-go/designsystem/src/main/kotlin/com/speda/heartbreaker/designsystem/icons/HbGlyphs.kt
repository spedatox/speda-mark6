package com.speda.heartbreaker.designsystem.icons

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Matrix
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * The app's stroked chrome glyphs, drawn on Canvas from the SAME 24-viewBox
 * geometry as the inline SVGs in the renderer components (Header.tsx,
 * Sidebar.tsx, HudFrame.tsx, InputBar.tsx). Canvas rather than ImageVector
 * because most of these are circles + lines, which path data expresses badly.
 *
 * No Material icons anywhere — silhouettes must match the web exactly.
 */
object HbGlyphs {

    /** Hamburger — Header.tsx "Open panel" / Sidebar close. */
    @Composable
    fun Menu(color: Color, size: Dp = 14.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        line(3f, 6f, 21f, 6f, color); line(3f, 12f, 21f, 12f, color); line(3f, 18f, 21f, 18f, color)
    }

    /** Magnifier — Sidebar search / Header. */
    @Composable
    fun Search(color: Color, size: Dp = 13.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        circle(11f, 11f, 8f, color)
        line(21f, 21f, 16.65f, 16.65f, color)
    }

    /** Command table — the roster converging on a centre point (WAR ROOM). */
    @Composable
    fun WarRoom(color: Color, size: Dp = 11.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        circle(12f, 12f, 3f, color)
        circle(12f, 3.5f, 1.6f, color); circle(19.5f, 16.5f, 1.6f, color); circle(4.5f, 16.5f, 1.6f, color)
        line(12f, 5.1f, 12f, 9f, color)
        line(18.1f, 15.6f, 14.6f, 13.5f, color)
        line(5.9f, 15.6f, 9.4f, 13.5f, color)
    }

    /** Three linked nodes (COMMS). */
    @Composable
    fun Comms(color: Color, size: Dp = 11.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        circle(5f, 12f, 2.4f, color); circle(19f, 5f, 2.4f, color); circle(19f, 19f, 2.4f, color)
        line(7.2f, 11f, 16.8f, 5.9f, color); line(7.2f, 13f, 16.8f, 18.1f, color)
    }

    /** Four-pane grid (SYS). */
    @Composable
    fun Sys(color: Color, size: Dp = 11.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        rect(3f, 3f, 7f, 7f, color); rect(14f, 3f, 7f, 7f, color)
        rect(3f, 14f, 7f, 7f, color); rect(14f, 14f, 7f, 7f, color)
    }

    /** Plus — new conversation / composer overflow. */
    @Composable
    fun Plus(color: Color, size: Dp = 13.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        line(12f, 5f, 12f, 19f, color); line(5f, 12f, 19f, 12f, color)
    }

    /** Sliders — settings / controls (three tracks with offset knobs). */
    @Composable
    fun Sliders(color: Color, size: Dp = 13.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        line(4f, 7f, 20f, 7f, color); line(4f, 12f, 20f, 12f, color); line(4f, 17f, 20f, 17f, color)
        circle(9f, 7f, 2f, color); circle(15f, 12f, 2f, color); circle(8f, 17f, 2f, color)
    }

    /** Send arrow (composer). */
    @Composable
    fun ArrowUp(color: Color, size: Dp = 14.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        line(12f, 19f, 12f, 5f, color)
        line(5f, 12f, 12f, 5f, color); line(12f, 5f, 19f, 12f, color)
    }

    /** Close / clear. */
    @Composable
    fun Close(color: Color, size: Dp = 13.dp, modifier: Modifier = Modifier) = Glyph(size, modifier, stroke = 2.5f) {
        line(18f, 6f, 6f, 18f, color); line(6f, 6f, 18f, 18f, color)
    }

    /** Chevron — dropdown affordances. */
    @Composable
    fun ChevronDown(color: Color, size: Dp = 8.dp, modifier: Modifier = Modifier) = Glyph(size, modifier, stroke = 3f) {
        line(6f, 9f, 12f, 15f, color); line(12f, 15f, 18f, 9f, color)
    }

    /** Chevron pointing up. */
    @Composable
    fun ChevronUp(color: Color, size: Dp = 10.dp, modifier: Modifier = Modifier) = Glyph(size, modifier, stroke = 2.5f) {
        line(18f, 15f, 12f, 9f, color); line(12f, 9f, 6f, 15f, color)
    }

    // ── Message action bar (Message.tsx icons — path data copied verbatim) ────

    /** Copy — two overlapping sheets. */
    @Composable
    fun Copy(color: Color, size: Dp = 15.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        pathString("M9 9h13v13H9z", color) // rect x9 y9 w13 h13 (rx≈2 reads square at this size)
        pathString("M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1", color)
    }

    /** Check — copied/confirmed. */
    @Composable
    fun Check(color: Color, size: Dp = 15.dp, modifier: Modifier = Modifier) = Glyph(size, modifier, stroke = 2.5f) {
        line(20f, 6f, 9f, 17f, color); line(9f, 17f, 4f, 12f, color)
    }

    /** Thumbs up — good response. */
    @Composable
    fun ThumbUp(color: Color, size: Dp = 15.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        pathString("M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3z", color)
        pathString("M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3", color)
    }

    /** Thumbs down — bad response. */
    @Composable
    fun ThumbDown(color: Color, size: Dp = 15.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        pathString("M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3z", color)
        pathString("M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17", color)
    }

    /** Refresh — regenerate response. */
    @Composable
    fun Refresh(color: Color, size: Dp = 15.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        line(23f, 4f, 23f, 10f, color); line(23f, 10f, 17f, 10f, color)
        pathString("M20.49 15a9 9 0 1 1-2.12-9.36L23 10", color)
    }

    /** Speaker — read aloud. */
    @Composable
    fun Speaker(color: Color, size: Dp = 15.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        pathString("M11 5 6 9H2v6h4l5 4z", color)
        pathString("M19.07 4.93a10 10 0 0 1 0 14.14", color)
        pathString("M15.54 8.46a5 5 0 0 1 0 7.07", color)
    }

    /** Pencil — edit message. */
    @Composable
    fun Edit(color: Color, size: Dp = 14.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        pathString("M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7", color)
        pathString("M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z", color)
    }

    /** Trash — delete message. */
    @Composable
    fun Trash(color: Color, size: Dp = 14.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        line(3f, 6f, 21f, 6f, color)
        pathString("M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6", color)
        line(10f, 11f, 10f, 17f, color); line(14f, 11f, 14f, 17f, color)
        pathString("M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2", color)
    }

    // ── drawing plumbing (24-viewBox → px) ──────────────────────────────────

    @Composable
    private fun Glyph(size: Dp, modifier: Modifier, stroke: Float = 2f, body: GlyphScope.() -> Unit) {
        Canvas(modifier.size(size)) {
            val k = this.size.minDimension / 24f
            GlyphScope(this, k, stroke * k).body()
        }
    }

    class GlyphScope(private val ds: DrawScope, private val k: Float, private val sw: Float) {
        fun line(x1: Float, y1: Float, x2: Float, y2: Float, color: Color) = ds.drawLine(
            color = color,
            start = Offset(x1 * k, y1 * k),
            end = Offset(x2 * k, y2 * k),
            strokeWidth = sw,
            cap = StrokeCap.Round,
        )

        fun circle(cx: Float, cy: Float, r: Float, color: Color) = ds.drawCircle(
            color = color,
            radius = r * k,
            center = Offset(cx * k, cy * k),
            style = Stroke(width = sw),
        )

        fun rect(x: Float, y: Float, w: Float, h: Float, color: Color) = ds.drawRect(
            color = color,
            topLeft = Offset(x * k, y * k),
            size = Size(w * k, h * k),
            style = Stroke(width = sw),
        )

        /**
         * Stroke an arbitrary SVG path — the `d` string is copied VERBATIM from
         * the source SVG (arcs/curves the line/circle helpers can't express),
         * parsed in 24-viewBox space and scaled by [k]. Round cap+join to match.
         */
        fun pathString(d: String, color: Color) {
            val p = PathParser().parsePathString(d).toPath()
            p.transform(Matrix().apply { scale(k, k) })
            ds.drawPath(p, color, style = Stroke(width = sw, cap = StrokeCap.Round, join = StrokeJoin.Round))
        }
    }
}
