package com.speda.heartbreaker.designsystem.icons

import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/**
 * Inline 24-viewBox STROKED icons, the app's only icon source (no Material
 * icons — silhouettes must match). Each is a 1:1 port of the corresponding
 * inline SVG in the renderer components.
 *
 * M0 SCOPE: only the glyphs the foundation shell needs (chevron, close). The
 * full per-surface set (HUD marks, tool-feed glyphs, composer icons, …) is
 * ported alongside those surfaces in M1–M4, path-data copied directly from the
 * TSX source so stroke geometry is identical.
 */
object HbIcons {

    private fun stroked(name: String, pathData: androidx.compose.ui.graphics.vector.PathBuilder.() -> Unit): ImageVector =
        ImageVector.Builder(
            name = name,
            defaultWidth = 24.dp,
            defaultHeight = 24.dp,
            viewportWidth = 24f,
            viewportHeight = 24f,
        ).apply {
            path(
                stroke = SolidColor(Color.White), // tinted at draw time via LocalContentColor / tint
                strokeLineWidth = 2f,
                strokeLineCap = StrokeCap.Round,
                strokeLineJoin = StrokeJoin.Round,
                pathBuilder = pathData,
            )
        }.build()

    val ChevronDown: ImageVector = stroked("ChevronDown") {
        moveTo(6f, 9f); lineTo(12f, 15f); lineTo(18f, 9f)
    }

    val ChevronRight: ImageVector = stroked("ChevronRight") {
        moveTo(9f, 6f); lineTo(15f, 12f); lineTo(9f, 18f)
    }

    val Close: ImageVector = stroked("Close") {
        moveTo(6f, 6f); lineTo(18f, 18f)
        moveTo(18f, 6f); lineTo(6f, 18f)
    }
}
