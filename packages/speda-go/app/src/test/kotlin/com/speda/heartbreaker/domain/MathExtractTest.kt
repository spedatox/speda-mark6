package com.speda.heartbreaker.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The lifter runs on [MarkdownPrep.prepare] output, so every input here is piped
 * through that first — the same order the renderer uses. What is being guarded is
 * that TeX survives commonmark's inline parser untouched, and that nothing which
 * only *looks* like math (currency, a `$` in a code fence) gets lifted.
 */
class MathExtractTest {

    private fun lift(src: String) = MathExtract.extract(MarkdownPrep.prepare(src))

    @Test
    fun lifts_inline_and_display_math() {
        val r = lift("Einstein said \\(E = mc^2\\) and then \\[a^2 + b^2 = c^2\\]")
        assertEquals(listOf("E = mc^2", "a^2 + b^2 = c^2"), r.spans.map { it.tex })
        assertEquals(listOf(false, true), r.spans.map { it.display })
        assertFalse("delimiters must be gone", r.markdown.contains('$'))
    }

    @Test
    fun preserves_backslashes_that_commonmark_would_eat() {
        // The whole reason this class exists: `\\` is a row break, `\{` is a brace.
        val tex = "\\begin{bmatrix} 1 & 0 \\\\ 0 & 1 \\end{bmatrix}"
        val r = lift("\$\$" + tex + "\$\$")
        assertEquals(listOf(tex), r.spans.map { it.tex })
    }

    @Test
    fun underscores_inside_math_do_not_become_emphasis() {
        val r = lift("\$x_1 + x_2\$ and \$y_1 + y_2\$")
        assertEquals(listOf("x_1 + x_2", "y_1 + y_2"), r.spans.map { it.tex })
    }

    @Test
    fun currency_is_not_math() {
        // MarkdownPrep escapes `$` before a digit; the lifter must respect that.
        val r = lift("It costs \$5 today and \$10 tomorrow.")
        assertTrue("no spans expected, got ${r.spans}", r.spans.isEmpty())
    }

    @Test
    fun code_regions_are_left_alone() {
        val r = lift("Use `\$PATH` here.\n\n```sh\necho \$HOME\n```")
        assertTrue("no spans expected, got ${r.spans}", r.spans.isEmpty())
    }

    @Test
    fun restore_puts_the_source_back() {
        val src = MarkdownPrep.prepare("Given \\(x^2\\), solve \\[y = 2x\\]")
        val r = MathExtract.extract(src)
        assertTrue(MathExtract.hasPlaceholder(r.markdown))
        assertEquals(src, MathExtract.restore(r.markdown, r.spans))
    }

    @Test
    fun mount_points_carry_index_and_display_mode() {
        val r = lift("inline \\(a\\) then \\[b\\]")
        val html = MathExtract.toMountPoints(r.markdown, r.spans)
        assertTrue(html.contains("""<span class="hb-math" data-i="0" data-d="0"></span>"""))
        assertTrue(html.contains("""<span class="hb-math" data-i="1" data-d="1"></span>"""))
    }

    @Test
    fun text_without_math_is_returned_untouched() {
        val src = "Just a plain sentence."
        val r = MathExtract.extract(src)
        assertEquals(src, r.markdown)
        assertTrue(r.spans.isEmpty())
    }
}
