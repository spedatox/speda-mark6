// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.domain

/**
 * The markdown pre-processors from Message.tsx, ported verbatim in behaviour.
 * Pure and fixture-tested against the TS (see gen-chat-fixtures.ts).
 */
object MarkdownPrep {

    private val FENCE_START = Regex("([^\n])```")
    private val CODE_REGION = Regex("```[\\s\\S]*?```|`[^`]*`")
    private val CURRENCY = Regex("(?<![\$\\\\])\\\$(?=\\d)")
    private val DISPLAY_MATH = Regex("\\\\\\[([\\s\\S]+?)\\\\\\]")
    private val INLINE_MATH = Regex("\\\\\\(([\\s\\S]+?)\\\\\\)")
    private val FENCE = Regex("```")
    private val FENCED_BLOCK = Regex("```[\\s\\S]*?```")
    private val LONE_TICK = Regex("(?<!`)`(?!`)")

    /**
     * Ensure every ``` fence starts on its own line. Models sometimes emit
     * "...sentence.```html" with no preceding newline, which the parser ignores
     * (fences must start a line).
     */
    fun normalizeCodeFences(text: String): String =
        FENCE_START.replace(text) { "${it.groupValues[1]}\n```" }

    /**
     * Prepare math without breaking currency or code. Operates only on non-code
     * regions (fenced blocks and inline code are preserved):
     *  1. Escape a lone `$` directly before a digit — that's currency ($5, $10.5),
     *     not math. Critical for a financial assistant.
     *  2. Normalise alternate delimiters: \[ \] → $$ $$, \( \) → $ $.
     */
    fun prepareMath(text: String): String {
        // The TS uses String.split with a capturing group, which keeps the code
        // regions at odd indices. Kotlin's split drops groups, so walk the matches.
        val out = StringBuilder()
        var last = 0
        for (m in CODE_REGION.findAll(text)) {
            out.append(transformNonCode(text.substring(last, m.range.first)))
            out.append(m.value) // code — left untouched
            last = m.range.last + 1
        }
        out.append(transformNonCode(text.substring(last)))
        return out.toString()
    }

    private fun transformNonCode(seg: String): String {
        var s = CURRENCY.replace(seg) { "\\\$" }             // currency first
        s = DISPLAY_MATH.replace(s) { "\$\$${it.groupValues[1]}\$\$" }
        s = INLINE_MATH.replace(s) { "\$${it.groupValues[1]}\$" }
        return s
    }

    /**
     * Close any unclosed fences so a partially-streamed string doesn't produce
     * broken output.
     */
    fun sanitizePartialMarkdown(input: String): String {
        var text = input
        val fences = FENCE.findAll(text).count()
        if (fences % 2 != 0) text += "\n```"
        val stripped = FENCED_BLOCK.replace(text, "")
        val ticks = LONE_TICK.findAll(stripped).count()
        if (ticks % 2 != 0) text += "`"
        return text
    }

    /** The full pipeline a text segment gets before rendering (TextSegment). */
    fun prepare(text: String): String = prepareMath(normalizeCodeFences(sanitizePartialMarkdown(text)))

    /* ── Table leniency (no TS counterpart — see ProseText) ──────────────── */

    private val DELIM_CELL = Regex(":?-+:?")

    /** A line the GFM table parser would take as a row: leading pipe, and one more. */
    private fun isPipeRow(line: String): Boolean {
        val t = line.trim()
        return t.startsWith("|") && t.indexOf('|', 1) > 0
    }

    /** The `|---|:--:|` line that turns the row above it into a header. */
    private fun isDelimiterRow(line: String): Boolean {
        val t = line.trim()
        if ('-' !in t) return false
        val cells = t.trim('|').split('|')
        return cells.isNotEmpty() && cells.all { DELIM_CELL.matches(it.trim()) }
    }

    /**
     * Re-attach a table row that a blank line stranded from the table above it.
     *
     * GFM ends a table at the first blank line, so a footer the model wrote as
     *
     *     | Item | Amount |
     *     |---|---|
     *     | Rent | 8,000 |
     *
     *     | Total | 24,245.83 |
     *
     * renders as literal pipes in a paragraph. The row is only re-attached when a
     * real table sits directly above it and the row does not start a table of its
     * own (no delimiter line follows it), which leaves every well-formed document
     * byte-identical.
     */
    fun stitchTables(text: String): String {
        if ('|' !in text) return text
        val lines = text.split("\n")
        val out = ArrayList<String>(lines.size)
        var fenced = false
        var inTable = false
        var i = 0
        while (i < lines.size) {
            val line = lines[i]
            if (line.trimStart().startsWith("```")) {
                fenced = !fenced
                inTable = false
            } else if (fenced) {
                // code — left untouched
            } else if (inTable && line.isBlank()) {
                var j = i + 1
                while (j < lines.size && lines[j].isBlank()) j++
                val orphan = j < lines.size && isPipeRow(lines[j]) &&
                    (j + 1 >= lines.size || !isDelimiterRow(lines[j + 1]))
                if (orphan) {
                    i = j // the blank run is dropped; the row rejoins the table
                    continue
                }
                inTable = false
            } else if (inTable) {
                if (!isPipeRow(line)) inTable = false
            } else if (isPipeRow(line) && i + 1 < lines.size && isDelimiterRow(lines[i + 1])) {
                inTable = true
            }
            out += line
            i++
        }
        return out.joinToString("\n")
    }
}
