// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.domain

/**
 * What gets spoken, and where one utterance ends — a port of the pure half of
 * heartbreaker `lib/voice.ts`.
 *
 * Two jobs, both of which have to happen before a single byte reaches a speech
 * engine:
 *
 *  - [speakable] drops everything meant to be SEEN. A reply in voice mode
 *    carries prose and ARTEFACTS — a chart's JSON, a LaTeX derivation, a staged
 *    window — and handing an artefact to a synthesiser produces exactly what it
 *    sounds like: the owner listening to a machine recite
 *    `{"type":"line","xKey":"x"`. Nothing is summarised or announced in its
 *    place either: what the model SAYS about its windows is the model's job (the
 *    backend briefs it), and a client-side stand-in would be a second, worse
 *    narrator.
 *
 *  - [splitSentences] cuts the stream into utterances, holding the unterminated
 *    tail back. Speaking half a sentence is worse than speaking it a moment
 *    later, and the tail is always about to be completed by the next delta.
 *
 * Both are stateless except for the fence tracking [SpeakableFilter] carries,
 * because a delta stream can put the opening ``` in one chunk and the closing
 * one three chunks later.
 */

/* ── Sentence segmentation ───────────────────────────────────────────────── */

/* A period is not a sentence end nearly often enough to matter, and Turkish is
 * the worst case: "3." is an ordinal, so "3. toplantı" is mid-sentence. Guards,
 * in the order they fire:
 *   - a known abbreviation before the dot        ("vb.", "Dr.", "bkz.")
 *   - digits immediately either side             ("3.5", "1.000")
 *   - a bare number before the dot               ("3. madde", "2026. yıl")
 *   - no whitespace after                        (mid-token dot, URLs, files)
 * What remains is a real boundary. */
private val ABBREVIATIONS = setOf(
    // Turkish
    "vb", "vs", "örn", "bkz", "age", "çev", "ör", "hz", "dr", "prof", "doç",
    "av", "sn", "yy", "bl", "shf", "tl", "cad", "sok", "apt", "mah",
    // English / shared
    "mr", "mrs", "ms", "st", "no", "tel", "etc", "ie", "eg", "approx", "min",
    "max", "fig", "vol", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
    "sep", "sept", "oct", "nov", "dec",
)

private val TERMINATORS = setOf('.', '!', '?', '…', '\n')

private fun isAbbreviationBefore(text: String, dotIndex: Int): Boolean {
    var i = dotIndex - 1
    val word = StringBuilder()
    while (i >= 0 && text[i].isLetter()) {
        word.insert(0, text[i])
        i--
    }
    // lowercase() with the ROOT locale on purpose: under a Turkish locale
    // "I".lowercase() is "ı", and "IE"/"Dr" would stop matching the table.
    return word.isNotEmpty() && word.toString().lowercase() in ABBREVIATIONS
}

private fun isNumericBefore(text: String, dotIndex: Int): Boolean {
    var i = dotIndex - 1
    var seen = false
    while (i >= 0 && (text[i].isDigit() || text[i] == '.' || text[i] == ',')) {
        if (text[i].isDigit()) seen = true
        i--
    }
    // A bare number ending in a dot is a Turkish ordinal ("3. madde"), not an end.
    return seen && (i < 0 || !text[i].isLetter())
}

/** Complete sentences, plus the unterminated remainder still being written. */
data class Utterances(val sentences: List<String>, val rest: String)

fun splitSentences(text: String): Utterances {
    val sentences = ArrayList<String>()
    var start = 0
    var i = 0

    while (i < text.length) {
        val ch = text[i]
        if (ch !in TERMINATORS) { i++; continue }

        if (ch == '.') {
            val next = text.getOrNull(i + 1)
            if (next != null && next.isDigit()) { i++; continue }   // 3.5
            if (isAbbreviationBefore(text, i)) { i++; continue }    // vb.
            if (isNumericBefore(text, i)) { i++; continue }         // 3. madde
        }

        // Absorb a run of terminators and any closing punctuation ("?!", "…\"").
        var end = i
        while (end + 1 < text.length &&
            (text[end + 1] in TERMINATORS || text[end + 1] in "\"')]»")
        ) {
            end++
        }

        // A boundary needs whitespace (or the end of the buffer) after it —
        // otherwise it is inside a token: a URL, a filename, a version number.
        val after = text.getOrNull(end + 1)
        if (after != null && !after.isWhitespace()) { i++; continue }

        val piece = text.substring(start, end + 1).trim()
        if (piece.isNotEmpty()) sentences.add(piece)
        start = end + 1
        i = end + 1
    }

    return Utterances(sentences, text.substring(start))
}

/* ── What is speakable ───────────────────────────────────────────────────── */

/** Strip inline math delimiters. `$a = 1$` is worth hearing as "a = 1"; anything
 *  with a TeX command in it is a formula, not a value, and is dropped. */
private val INLINE_MATH = Regex("""\$([^$\n]+)\$""")
private val TEX_COMMAND = Regex("""\\[a-zA-Z]""")

private fun inlineMath(line: String): String =
    INLINE_MATH.replace(line) { m ->
        if (TEX_COMMAND.containsMatchIn(m.groupValues[1])) "" else m.groupValues[1]
    }

/**
 * Say the words, not the notation. Even with the backend asking for plain spoken
 * prose, a model will still reach for a heading or a bullet out of habit, and
 * "hash hash Standard Form" is the same failure as reading LaTeX.
 */
private val PROSE_RULES = listOf(
    Regex("""^\s{0,3}#{1,6}\s+""") to "",            // headings
    Regex("""^\s*[-*+]\s+""") to "",                  // bullets
    Regex("""^\s*\d+\.\s+""") to "",                  // numbered items
    Regex("""^\s*>\s?""") to "",                      // block quotes
    Regex("""\*\*([^*]+)\*\*""") to "$1",             // bold
    Regex("""(^|\W)\*([^*\n]+)\*""") to "$1$2",       // italics
    Regex("""`([^`\n]+)`""") to "$1",                 // inline code
)

private fun spokenProse(line: String): String =
    PROSE_RULES.fold(line) { acc, (re, replacement) -> re.replace(acc, replacement) }

/** A row of a pipe table. The table is an artefact — it gets its own window on
 *  the board — and a speech engine handed one says "pipe root pipe value pipe". */
private fun isTableRow(t: String): Boolean =
    t.startsWith("|") && t.endsWith("|") && t.length > 1

/**
 * Drops everything that belongs on the board rather than in the ear.
 *
 * Stateful because the stream is: an opening ``` can arrive three deltas before
 * its closing one, so whether a given line is inside a fence is not decidable
 * from that line alone. One instance per turn; construct a new one for the next.
 *
 * Feed it only COMPLETE lines — a half-written line cannot be judged either, and
 * the sentence splitter is holding the tail anyway.
 */
class SpeakableFilter {
    private var inFence = false
    private var inMath = false

    fun speakable(text: String): String {
        val out = StringBuilder()
        for (line in text.split('\n')) {
            val t = line.trim()
            if (inFence) {
                if (t.startsWith("```")) inFence = false
                continue
            }
            if (t.startsWith("```")) { inFence = true; continue }
            if (inMath) {
                if (t.contains("$$") || t.contains("""\]""")) inMath = false
                continue
            }
            if (t.startsWith("$$") || t.startsWith("""\[""")) {
                // A one-line `$$x$$` opens and closes at once.
                val closed = t.length > 2 && (t.endsWith("$$") || t.endsWith("""\]"""))
                inMath = !closed
                continue
            }
            if (isTableRow(t)) continue
            out.append(spokenProse(inlineMath(line))).append('\n')
        }
        return out.toString()
    }
}
