package com.speda.heartbreaker.domain

/** One `$…$` / `$$…$$` region lifted out of the markdown before it is parsed. */
data class MathSpan(val tex: String, val display: Boolean)

/** The markdown with math swapped for placeholders, plus the spans in order. */
data class ExtractedMath(val markdown: String, val spans: List<MathSpan>)

/**
 * Math has to leave the markdown BEFORE commonmark sees it.
 *
 * The web gets away with `remark-math` because the plugin runs inside the same
 * parser and claims the `$…$` runs before the inline phase. We only have stock
 * commonmark, whose inline parser eats exactly the characters LaTeX needs most:
 * `\\` (matrix/align row break) collapses to `\`, `\{` collapses to `{`, and a
 * lone `_` or `*` inside a formula opens an emphasis run that swallows the rest
 * of the paragraph. By the time the AST exists the formula is already wrong.
 *
 * So we lift every math region out first, leave a private-use placeholder in its
 * place (U+E000 index U+E001 — inert to every markdown construct, and preserved
 * verbatim by commonmark as literal text), and stitch the real TeX back in after
 * rendering. Runs on [MarkdownPrep.prepare] output, so delimiters are already
 * normalised to `$`/`$$` and currency is already escaped to `\$`.
 */
object MathExtract {

    const val OPEN = '\uE000'
    const val CLOSE = '\uE001'

    private val CODE_REGION = Regex("```[\\s\\S]*?```|`[^`]*`")

    /**
     * Display and inline in ONE alternation, display first so `$$` is never read
     * as an empty inline run. A pass each would number the spans by delimiter kind
     * rather than by position, which is the sort of ordering nobody notices until
     * a formula renders as the wrong formula.
     */
    private val MATH = Regex(
        "(?<!\\\\)\\\$\\\$([\\s\\S]+?)(?<!\\\\)\\\$\\\$" +
            "|(?<!\\\\)\\\$([^\\\$\n]+?)(?<!\\\\)\\\$",
    )
    private val PLACEHOLDER = Regex("$OPEN(\\d+)$CLOSE")

    fun extract(markdown: String): ExtractedMath {
        if (!markdown.contains('$')) return ExtractedMath(markdown, emptyList())

        val spans = mutableListOf<MathSpan>()
        val out = StringBuilder()
        var last = 0
        // Code regions are copied through untouched — `$x$` inside a fence is code.
        for (m in CODE_REGION.findAll(markdown)) {
            out.append(lift(markdown.substring(last, m.range.first), spans))
            out.append(m.value)
            last = m.range.last + 1
        }
        out.append(lift(markdown.substring(last), spans))
        return ExtractedMath(out.toString(), spans)
    }

    private fun lift(seg: String, spans: MutableList<MathSpan>): String =
        MATH.replace(seg) { m ->
            val display = m.groupValues[1].isNotEmpty()
            val tex = if (display) m.groupValues[1] else m.groupValues[2]
            spans += MathSpan(tex.trim(), display)
            OPEN.toString() + (spans.size - 1) + CLOSE
        }

    fun hasPlaceholder(s: String): Boolean = s.indexOf(OPEN) >= 0

    /**
     * Put the original `$…$` source back. The safety net for any placeholder that
     * reaches a renderer with no math support (a heading, a table cell) — the user
     * sees the TeX they wrote, never a stray private-use glyph.
     */
    fun restore(s: String, spans: List<MathSpan>): String =
        if (!hasPlaceholder(s)) {
            s
        } else {
            PLACEHOLDER.replace(s) { m ->
                val span = spans.getOrNull(m.groupValues[1].toInt()) ?: return@replace m.value
                val d = if (span.display) "\$\$" else "\$"
                d + span.tex + d
            }
        }

    /** Rewrite placeholders into the KaTeX mount points the WebView shell fills in. */
    fun toMountPoints(html: String, spans: List<MathSpan>): String =
        PLACEHOLDER.replace(html) { m ->
            val i = m.groupValues[1].toInt()
            val span = spans.getOrNull(i) ?: return@replace m.value
            """<span class="hb-math" data-i="$i" data-d="${if (span.display) 1 else 0}"></span>"""
        }
}
