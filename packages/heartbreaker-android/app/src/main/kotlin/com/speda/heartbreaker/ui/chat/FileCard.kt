package com.speda.heartbreaker.ui.chat

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.Downloader
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.FileMeta
import kotlinx.coroutines.launch

/**
 * A downloadable file the agent produced this turn — port of FileCard in
 * Message.tsx: a glass slab with a tinted doc glyph, the title, a "KIND · SIZE"
 * readout, and the amber DOWNLOAD action chip.
 */
@Composable
fun FileCard(
    file: FileMeta,
    config: AppConfig,
    downloader: Downloader,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var done by remember { mutableStateOf(false) }

    Row(
        modifier
            .widthIn(max = 420.dp)
            .padding(top = 10.dp)
            .hbGlass(shape = HbGlassShape.R12)
            .padding(horizontal = 11.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(11.dp),
    ) {
        // Doc glyph in a tinted square
        Box(
            Modifier
                .size(38.dp)
                .clip(RoundedCornerShape(9.dp))
                .background(palette.accent.copy(alpha = 0.14f))
                .border(1.dp, palette.accent.copy(alpha = 0.28f), RoundedCornerShape(9.dp)),
            contentAlignment = Alignment.Center,
        ) { DocGlyph(palette.accentBright) }

        // Title + meta
        Column(Modifier.weight(1f)) {
            BasicText(
                AnnotatedString(file.title.ifBlank { file.name }),
                style = HbType.read.copy(fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = palette.text),
                maxLines = 1,
            )
            BasicText(
                AnnotatedString("${file.kind} · ${fmtBytes(file.size)}"),
                style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.06.em, color = palette.textFaint),
                modifier = Modifier.padding(top = 2.dp),
            )
        }

        // Amber DOWNLOAD chip
        Row(
            Modifier
                .clip(RoundedCornerShape(9.dp))
                .background(Color(0xFFD99C44).copy(alpha = if (busy) 0.10f else 0.16f))
                .border(1.dp, Color(0xFFF2B75C).copy(alpha = 0.45f), RoundedCornerShape(9.dp))
                .clickable(enabled = !busy) {
                    busy = true
                    scope.launch {
                        val saved = downloader.download(config, file.url, file.name)
                        busy = false
                        done = saved != null
                    }
                }
                .padding(horizontal = 10.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            DownloadGlyph(Color(0xFFF6D9A8))
            BasicText(
                AnnotatedString(if (busy) "…" else if (done) "SAVED" else "DOWNLOAD"),
                style = HbType.headerBar.copy(
                    fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.1.em,
                    color = if (done) palette.green else Color(0xFFF6D9A8),
                ),
            )
        }
    }
}

/** Message.tsx fmtBytes. */
internal fun fmtBytes(b: Long): String = when {
    b < 1024 -> "$b B"
    b < 1024 * 1024 -> "${(b / 1024.0).toInt()} KB"
    else -> String.format(java.util.Locale.ENGLISH, "%.1f MB", b / 1024.0 / 1024.0)
}

/** The folded-corner document glyph (24 viewBox, as the inline SVG). */
@Composable
private fun DocGlyph(color: Color, size: androidx.compose.ui.unit.Dp = 18.dp) {
    Canvas(Modifier.size(size)) {
        val k = this.size.minDimension / 24f
        val w = 1.8f * k
        val p = androidx.compose.ui.graphics.Path().apply {
            moveTo(14f * k, 2f * k)
            lineTo(6f * k, 2f * k)
            lineTo(4f * k, 4f * k)
            lineTo(4f * k, 20f * k)
            lineTo(6f * k, 22f * k)
            lineTo(18f * k, 22f * k)
            lineTo(20f * k, 20f * k)
            lineTo(20f * k, 8f * k)
            close()
        }
        drawPath(p, color, style = Stroke(width = w))
        // polyline 14 2 / 14 8 / 20 8
        drawLine(color, Offset(14f * k, 2f * k), Offset(14f * k, 8f * k), w, StrokeCap.Round)
        drawLine(color, Offset(14f * k, 8f * k), Offset(20f * k, 8f * k), w, StrokeCap.Round)
    }
}

/** Tray-with-arrow download glyph. */
@Composable
private fun DownloadGlyph(color: Color, size: androidx.compose.ui.unit.Dp = 12.dp) {
    Canvas(Modifier.size(size)) {
        val k = this.size.minDimension / 24f
        val w = 2f * k
        drawLine(color, Offset(12f * k, 3f * k), Offset(12f * k, 15f * k), w, StrokeCap.Round)
        drawLine(color, Offset(7f * k, 10f * k), Offset(12f * k, 15f * k), w, StrokeCap.Round)
        drawLine(color, Offset(17f * k, 10f * k), Offset(12f * k, 15f * k), w, StrokeCap.Round)
        drawLine(color, Offset(3f * k, 15f * k), Offset(3f * k, 19f * k), w, StrokeCap.Round)
        drawLine(color, Offset(3f * k, 19f * k), Offset(21f * k, 19f * k), w, StrokeCap.Round)
        drawLine(color, Offset(21f * k, 19f * k), Offset(21f * k, 15f * k), w, StrokeCap.Round)
    }
}
