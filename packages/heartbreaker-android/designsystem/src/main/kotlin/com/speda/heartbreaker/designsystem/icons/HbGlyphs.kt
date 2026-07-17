package com.speda.heartbreaker.designsystem.icons

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
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

    /** Send arrow (composer). */
    @Composable
    fun ArrowUp(color: Color, size: Dp = 14.dp, modifier: Modifier = Modifier) = Glyph(size, modifier) {
        line(12f, 19f, 12f, 5f, color)
        line(5f, 12f, 12f, 5f, color); line(12f, 5f, 19f, 12f, color)
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
    }
}
