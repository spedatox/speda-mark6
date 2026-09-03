// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.prose

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.BoardKind
import com.speda.heartbreaker.domain.StatDir
import com.speda.heartbreaker.domain.boardFields
import com.speda.heartbreaker.domain.boardRows
import com.speda.heartbreaker.domain.quoteBody
import com.speda.heartbreaker.domain.statDirection
import com.speda.heartbreaker.domain.timelineRows

/**
 * The presentation kinds, rendered — the phone's half of the voice canvas
 * (heartbreaker `components/VoicePanelBody.tsx`).
 *
 * On the desktop these float on a board beside a docked orb. Here they are laid
 * out down the message, full-width and in the order the agent staged them,
 * because that IS the board on a phone: floating, overlapping, hand-resized
 * windows are a mouse gesture, and a vertical deck reads the same content in
 * the same order without pretending otherwise.
 *
 * Every one of these reuses [ChartPanel] for its shell, so a staged window
 * wears exactly the chrome every other rich block wears — one material, one
 * header, one MAIN_SUB split.
 */
@Composable
fun BoardBlock(kind: BoardKind, title: String, body: String, modifier: Modifier = Modifier) {
    ChartPanel(title = title, modifier = modifier) {
        Box(Modifier.padding(horizontal = 12.dp)) {
            when (kind) {
                BoardKind.STAT -> StatWindow(body)
                BoardKind.IMAGE -> ImageWindow(body)
                BoardKind.ARTICLE -> ArticleWindow(body)
                BoardKind.CARD -> CardWindow(body)
                BoardKind.TIMELINE -> TimelineWindow(body)
                BoardKind.QUOTE -> QuoteWindow(body)
            }
        }
    }
}

/** The small caps label these windows use for every field name and byline. */
@Composable
private fun FieldLabel(text: String, modifier: Modifier = Modifier) {
    val palette = LocalHbPalette.current
    BasicText(
        AnnotatedString(text.uppercase()),
        modifier = modifier,
        style = HbType.label.copy(fontSize = 9.5.sp, color = palette.textFaint),
    )
}

/** A picture, or nothing at all — see [rememberBoardImage] for why nothing is
 *  the deliberate outcome rather than a placeholder. */
@Composable
private fun Shot(url: String, height: Int, modifier: Modifier = Modifier) {
    val palette = LocalHbPalette.current
    val image = rememberBoardImage(url) ?: return
    Image(
        bitmap = image,
        contentDescription = null,
        contentScale = ContentScale.Crop,
        modifier = modifier
            .fillMaxWidth()
            .height(height.dp)
            .clip(RoundedCornerShape(6.dp))
            .border(1.dp, palette.line, RoundedCornerShape(6.dp)),
    )
}

/* ── stat ──────────────────────────────────────────────────────────────────
 * Line 1 the value, line 2 the change, line 3 an optional caption. The one kind
 * whose whole job is to be read at a glance, so the value is set as large as
 * the row allows and everything else is annotation around it. */
@Composable
private fun StatWindow(body: String) {
    val palette = LocalHbPalette.current
    val rows = boardRows(body)
    val value = rows.getOrNull(0).orEmpty()
    val delta = rows.getOrNull(1).orEmpty()
    val caption = rows.drop(2).joinToString(" ")

    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        BasicText(
            AnnotatedString(value),
            style = HbType.headerBar.copy(
                fontSize = 30.sp, fontWeight = FontWeight.Bold,
                letterSpacing = 0.01.em, color = palette.text,
            ),
        )
        if (delta.isNotBlank()) {
            BasicText(
                AnnotatedString(delta),
                style = HbType.headerBar.copy(
                    fontSize = 13.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.06.em,
                    color = when (statDirection(delta)) {
                        StatDir.DOWN -> palette.red
                        StatDir.UP -> palette.green
                        StatDir.FLAT -> palette.textDim
                    },
                ),
            )
        }
        if (caption.isNotBlank()) FieldLabel(caption)
    }
}

/* ── image ─────────────────────────────────────────────────────────────────
 * A URL on line 1, an optional caption after it. */
@Composable
private fun ImageWindow(body: String) {
    val palette = LocalHbPalette.current
    val rows = boardRows(body)
    val url = rows.firstOrNull().orEmpty()
    val caption = rows.drop(1).joinToString(" ")
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Shot(url, height = 200)
        if (caption.isNotBlank()) {
            BasicText(
                AnnotatedString(caption),
                style = HbType.read.copy(fontSize = 12.sp, color = palette.textDim),
            )
        }
    }
}

/* ── article ───────────────────────────────────────────────────────────────
 * The newspaper cutting: source, date, headline, and the excerpt that mattered.
 * The window the whole redesign was argued from — asked to research someone, an
 * agent puts the articles on the wall rather than reading summaries out. */
@Composable
private fun ArticleWindow(body: String) {
    val palette = LocalHbPalette.current
    val (meta, rest) = boardFields(body)
    val title = meta["title"] ?: meta["headline"].orEmpty()

    Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
        meta["image"]?.let { Shot(it, height = 150) }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            meta["source"]?.let {
                BasicText(
                    AnnotatedString(it.uppercase()),
                    style = HbType.label.copy(fontSize = 9.5.sp, color = palette.accent),
                )
            }
            meta["date"]?.let { FieldLabel(it) }
        }
        if (title.isNotBlank()) {
            BasicText(
                AnnotatedString(title),
                style = HbType.headerBar.copy(
                    fontSize = 15.sp, fontWeight = FontWeight.Bold,
                    letterSpacing = 0.02.em, lineHeight = 1.3.em, color = palette.text,
                ),
            )
        }
        if (rest.isNotBlank()) {
            BasicText(
                AnnotatedString(rest),
                style = HbType.read.copy(fontSize = 13.sp, color = palette.textDim),
            )
        }
        // The URL is never printed. It is not read aloud and nobody types one
        // off a screen; on the desktop the headline carries the link, and here
        // there is nothing to click through to yet.
    }
}

/* ── card ──────────────────────────────────────────────────────────────────
 * A name, a photo, and a column of `Field: value`. Whatever the turn is ABOUT,
 * as a file rather than as a paragraph describing it. */
@Composable
private fun CardWindow(body: String) {
    val palette = LocalHbPalette.current
    val lines = body.split('\n')
    // The first line is the name only if it is not itself a field — a body that
    // opens straight into `Role: …` is a file with no title, which is fine.
    val headIsField = Regex("""^\s*[A-Za-z][\w .-]{0,40}?\s*:""").containsMatchIn(lines.firstOrNull().orEmpty())
    val name = if (headIsField) "" else lines.firstOrNull().orEmpty().trim()
    val (meta, rest) = boardFields(if (headIsField) body else lines.drop(1).joinToString("\n"))
    val photo = meta["image"] ?: meta["photo"]
    val entries = meta.filterKeys { it != "image" && it != "photo" }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        photo?.let { Shot(it, height = 190) }
        if (name.isNotBlank()) {
            BasicText(
                AnnotatedString(name),
                style = HbType.headerBar.copy(
                    fontSize = 16.sp, fontWeight = FontWeight.Bold,
                    letterSpacing = 0.04.em, color = palette.text,
                ),
            )
        }
        entries.forEach { (k, v) ->
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                FieldLabel(k, Modifier.width(96.dp).padding(top = 3.dp))
                BasicText(
                    AnnotatedString(v),
                    style = HbType.read.copy(fontSize = 13.sp, color = palette.text),
                )
            }
        }
        if (rest.isNotBlank()) {
            BasicText(
                AnnotatedString(rest),
                style = HbType.read.copy(fontSize = 13.sp, color = palette.textDim),
            )
        }
    }
}

/* ── timeline ──────────────────────────────────────────────────────────────
 * One `date — what happened` per line. A sequence is what prose is worst at and
 * a dated column is best at, which is exactly why it is a kind: spoken, six
 * dates in a row are unfollowable. */
@Composable
private fun TimelineWindow(body: String) {
    val palette = LocalHbPalette.current
    Column {
        timelineRows(body).forEachIndexed { n, row ->
            // A hairline between events, inset past the rail so the markers
            // read as one column rather than as rows in a table.
            if (n > 0) {
                Box(
                    Modifier
                        .padding(start = 20.dp)
                        .fillMaxWidth()
                        .height(1.dp)
                        .background(palette.line),
                )
            }
            Row(
                Modifier.padding(vertical = 6.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                // The rail: a marker per event, so the column reads as a
                // sequence rather than a list that happens to start with dates.
                BasicText(
                    AnnotatedString("◆"),
                    modifier = Modifier.padding(top = 3.dp),
                    style = HbType.readout.copy(fontSize = 9.sp, color = palette.accentDim),
                )
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    if (row.whenLabel.isNotBlank()) FieldLabel(row.whenLabel)
                    BasicText(
                        AnnotatedString(row.what),
                        style = HbType.read.copy(fontSize = 13.sp, color = palette.text),
                    )
                }
            }
        }
    }
}

/* ── quote ─────────────────────────────────────────────────────────────────
 * Exists so a source's own words go up verbatim instead of being paraphrased
 * into the narration, which is the one thing a research readout must not do. */
@Composable
private fun QuoteWindow(body: String) {
    val palette = LocalHbPalette.current
    val q = quoteBody(body)
    Row(
        Modifier.height(IntrinsicSize.Min),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        // The rule down the left is the quotation mark. IntrinsicSize so it
        // matches the quote's own height instead of being given one.
        Box(
            Modifier
                .width(2.dp)
                .fillMaxHeight()
                .clip(RoundedCornerShape(1.dp))
                .background(palette.lineBright),
        )
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            BasicText(
                AnnotatedString(q.text),
                style = HbType.read.copy(
                    fontSize = 14.sp, fontStyle = FontStyle.Italic, color = palette.text,
                ),
            )
            if (q.attribution.isNotBlank()) FieldLabel("— ${q.attribution}")
        }
    }
}
