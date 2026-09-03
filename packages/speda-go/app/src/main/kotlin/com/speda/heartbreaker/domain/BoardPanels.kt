// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.domain

/**
 * The presentation vocabulary's pure half — a port of the parsing in
 * heartbreaker `lib/voicePanels.ts` and `components/VoicePanelBody.tsx`.
 *
 * ── What these blocks are ───────────────────────────────────────────────────
 * When an agent is presenting rather than answering (igor `core/surface.py`
 * `_VOICE_BRIEF`), it stops speaking facts that could be SHOWN and stages them
 * as windows instead: a figure as a tile, a source as a cutting with its photo,
 * a person as a file, a sequence as a timeline. Each is a fenced block whose
 * info line is `kind | SCREEN TITLE`.
 *
 * ── Why the phone renders them at all ───────────────────────────────────────
 * The desktop shows these on a floating board beside a docked orb. There is no
 * voice mode here yet and floating windows are the wrong gesture on a phone
 * anyway — so on this client the board IS the message flow: the windows appear
 * full-width, in the order the agent staged them, each under its own header.
 * Same content, same order, laid out the way a phone reads.
 *
 * That also closes a gap rather than only adding a feature: without these, a
 * `card` or a `timeline` falls through to the code-block renderer and the owner
 * gets raw text with colons in it.
 *
 * ── Forgiving on purpose ────────────────────────────────────────────────────
 * These bodies are written by a model mid-sentence, under a word budget, while
 * narrating. Every parser here degrades — a missing field, a stray blank line,
 * a label the brief never mentioned makes a window plainer, never empty and
 * never a crash. If a line cannot be understood it is shown as text.
 */

enum class BoardKind { STAT, IMAGE, ARTICLE, CARD, TIMELINE, QUOTE }

private val KIND_BY_NAME = mapOf(
    "stat" to BoardKind.STAT,
    "image" to BoardKind.IMAGE,
    "article" to BoardKind.ARTICLE,
    "card" to BoardKind.CARD,
    "timeline" to BoardKind.TIMELINE,
    "quote" to BoardKind.QUOTE,
)

/** Fallback label for a window the agent left untitled. */
private val LABEL = mapOf(
    BoardKind.STAT to "FIGURE",
    BoardKind.IMAGE to "IMAGE",
    BoardKind.ARTICLE to "SOURCE",
    BoardKind.CARD to "FILE",
    BoardKind.TIMELINE to "TIMELINE",
    BoardKind.QUOTE to "QUOTE",
)

/** A fence's info line, split into the kind and the title the agent chose. */
data class FenceInfo(val lang: String, val title: String)

/**
 * `chart | REVENUE / MONTHLY` → kind + title. The separator is optional: a bare
 * ```chart is still a chart, it just gets a generated label.
 */
fun parseFenceInfo(info: String): FenceInfo {
    val bar = info.indexOf('|')
    if (bar < 0) return FenceInfo(info.trim().lowercase(), "")
    return FenceInfo(info.substring(0, bar).trim().lowercase(), info.substring(bar + 1).trim())
}

/** The board kind for a fence language, or null if this is not a staged window. */
fun boardKindOf(lang: String): BoardKind? = KIND_BY_NAME[lang]

/**
 * Titles render as `MAIN_SUB` — white up to the underscore, accent after it,
 * the same split every panel header in the app uses. An authored title is
 * upper-cased and its first separator becomes that underscore, so
 * `REVENUE / MONTHLY` reads as `REVENUE` + `_MONTHLY`.
 */
fun boardTitle(raw: String, kind: BoardKind): String {
    val t = raw.trim().uppercase()
        .replace(Regex("""\s*[/|·—–-]\s*"""), "_")
        .replace(Regex("""\s+"""), " ")
    if (t.isBlank()) return "${LABEL[kind]}_"
    // A title with no separator still needs one, or the whole header renders in
    // the sub colour.
    return if (t.contains('_')) t else "${t}_"
}

/* ── Body shapes ─────────────────────────────────────────────────────────── */

/** Non-empty lines, trimmed — what most of these formats reduce to. */
fun boardRows(src: String): List<String> =
    src.split('\n').map { it.trim() }.filter { it.isNotEmpty() }

private val FIELD = Regex("""^\s*([A-Za-z][\w .-]{0,40}?)\s*:\s*(.*)$""")

/** `Field: value` lines from the head of a block, plus whatever came after.
 *  Stops at the first line that is not a field, so a body can lead with
 *  metadata and follow with prose without needing a separator. */
data class BoardFields(val meta: Map<String, String>, val rest: String)

fun boardFields(src: String): BoardFields {
    val lines = src.split('\n')
    val meta = LinkedHashMap<String, String>()
    var i = 0
    while (i < lines.size) {
        val line = lines[i]
        if (line.isBlank()) {
            if (meta.isNotEmpty()) { i++; break } else { i++; continue }
        }
        val m = FIELD.find(line) ?: break
        meta[m.groupValues[1].trim().lowercase()] = m.groupValues[2].trim()
        i++
    }
    return BoardFields(meta, lines.drop(i).joinToString("\n").trim())
}

/** Is this something we would hand to an image loader? Anything else in an
 *  image slot is a description the model wrote instead of a link. */
fun isImageUrl(s: String): Boolean =
    Regex("""^(https?://|data:image/)""", RegexOption.IGNORE_CASE).containsMatchIn(s.trim())

/** Which way a stat's change line points. Read from the SIGN rather than from a
 *  field the model has to remember to set — and anything ambiguous stays
 *  neutral rather than guessing, because a briefing that colours a neutral
 *  figure green has told the owner something untrue. */
enum class StatDir { UP, DOWN, FLAT }

fun statDirection(delta: String): StatDir = when {
    Regex("""^[-−↓]|\bdown\b|\bdüş""", RegexOption.IGNORE_CASE).containsMatchIn(delta) -> StatDir.DOWN
    Regex("""^[+↑]|\bup\b|\bart""", RegexOption.IGNORE_CASE).containsMatchIn(delta) -> StatDir.UP
    else -> StatDir.FLAT
}

/** One row of a timeline: `1963 — Born in Moscow`. A line with no separator is
 *  kept whole as the event, which is how an undated note still shows up. */
data class TimelineRow(val whenLabel: String, val what: String)

private val TIMELINE_SPLIT = Regex("""^(.{1,32}?)\s*[—–\-:|]\s+(.*)$""")

fun timelineRows(src: String): List<TimelineRow> = boardRows(src).map { line ->
    val m = TIMELINE_SPLIT.find(line)
    if (m != null) TimelineRow(m.groupValues[1].trim(), m.groupValues[2].trim())
    else TimelineRow("", line)
}

/** A quote and its attribution — the `— someone` line, wherever it sits. */
data class QuoteBody(val text: String, val attribution: String)

fun quoteBody(src: String): QuoteBody {
    val lines = boardRows(src)
    val at = lines.indexOfFirst { Regex("""^[—–-]\s+""").containsMatchIn(it) }
    val body = (if (at < 0) lines else lines.take(at)).joinToString(" ")
    val attr = if (at < 0) "" else lines[at].replace(Regex("""^[—–-]\s+"""), "")
    return QuoteBody(body, attr)
}
