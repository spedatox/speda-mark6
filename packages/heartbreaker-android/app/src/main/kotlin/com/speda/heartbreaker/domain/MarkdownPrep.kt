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
}
