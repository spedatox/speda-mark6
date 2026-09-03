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
 * ── Where the phone renders them ────────────────────────────────────────────
 * Two places, from the same parsing. In ORDINARY CHAT they render inline
 * wherever they appear, which is what stops a `card` falling through to the
 * code-block renderer and arriving as raw text with colons in it. In VOICE MODE
 * they are the board (`ui/voice/VoiceModeScreen`).
 *
 * The desktop floats those windows over a void and lets the owner drag and
 * resize them. Those are mouse gestures. Here the board is a COLUMN, in staged
 * order, full width — same content, same order, laid out the way a phone is
 * read.
 *
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

/* ── Reading a presentation ──────────────────────────────────────────────────
 * Voice mode's surface needs the reply cut two ways: the windows the agent
 * staged, in the order it staged them, and the narration it said around them.
 *
 * Order is the whole point. A window's position in the reply is the moment it
 * appears on screen — the stream arrives token by token, so a block written
 * between two spoken sentences materialises between those two being heard.
 * Nothing here has to sync against audio; writing order IS the cue track. */

/** One staged window, still in its raw fenced form. The `info` line is kept
 *  whole rather than pre-parsed so the existing fence dispatcher can render it —
 *  a chart on the board is then literally the same chart it is in chat, not a
 *  second implementation of one. */
data class StagedWindow(val info: String, val body: String)

/** The windows an agent staged, in order. */
fun splitBoardPanels(text: String): List<StagedWindow> {
    val out = ArrayList<StagedWindow>()
    val lines = text.split('\n')
    var i = 0
    while (i < lines.size) {
        val open = Regex("""^[ \t]*```[ \t]*(.*)$""").find(lines[i])
        if (open == null) { i++; continue }
        val info = open.groupValues[1].trim()
        val body = ArrayList<String>()
        i++
        while (i < lines.size && !lines[i].trimStart().startsWith("```")) body.add(lines[i++])
        // An UNCLOSED fence is the one still being written. It is dropped rather
        // than rendered: a half-arrived chart spec parses as garbage, and a
        // window that flickers through malformed states while it streams is
        // worse than one that appears when it is ready.
        if (i >= lines.size) break
        i++
        out.add(StagedWindow(info, body.joinToString("\n")))
    }
    return out
}

/**
 * The narration alone — everything outside a staged window, as one running
 * string for the caption.
 *
 * This is the same cut the speech path makes ([SpeakableFilter]), and
 * deliberately so: a subtitle has to say what is being SAID. It is a second
 * implementation rather than a shared one because the two run on different
 * material — the filter judges a live delta stream line by line and carries
 * fence state between calls, while this re-reads the whole visible text each
 * time it is asked. Sharing the traversal would mean giving the streaming path
 * state it does not need, or this one state it should not have.
 */
fun captionOf(text: String): String {
    val out = ArrayList<String>()
    var inFence = false
    var inMath = false
    for (line in text.split('\n')) {
        val t = line.trim()
        if (inFence) { if (t.startsWith("```")) inFence = false; continue }
        if (t.startsWith("```")) { inFence = true; continue }
        if (inMath) { if (t.contains("$$") || t.contains("""\]""")) inMath = false; continue }
        if (t.startsWith("$$") || t.startsWith("""\[""")) {
            inMath = !(t.length > 2 && (t.endsWith("$$") || t.endsWith("""\]""")))
            continue
        }
        if (t.startsWith("|") && t.endsWith("|") && t.length > 1) continue
        out.add(
            line
                .replace(Regex("""^\s{0,3}#{1,6}\s+"""), "")
                .replace(Regex("""^\s*[-*+]\s+"""), "")
                .replace(Regex("""^\s*\d+\.\s+"""), "")
                .replace(Regex("""^\s*>\s?"""), "")
                .replace(Regex("""\*\*([^*]+)\*\*"""), "$1")
                .replace(Regex("""(^|\W)\*([^*\n]+)\*"""), "$1$2")
                .replace(Regex("""`([^`\n]+)`"""), "$1"),
        )
    }
    return out.joinToString("\n").replace(Regex("""\n{3,}"""), "\n\n").trim()
}
