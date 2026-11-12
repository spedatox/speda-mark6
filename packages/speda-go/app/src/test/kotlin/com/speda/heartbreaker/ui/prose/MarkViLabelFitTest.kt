// SPDX-License-Identifier: AGPL-3.0-or-later
package com.speda.heartbreaker.ui.prose

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkViLabelFitTest {
    private val css = """
        svg.mkvi-diagram .mkvi-label { font-size: 26px; }
        svg.mkvi-diagram .mkvi-note { font-size: 21px; }
    """.trimIndent()

    @Test
    fun fitsOverflowingNotesWithoutChangingLabelsOrCaptionsThatFit() {
        val svg = """
            <svg xmlns="http://www.w3.org/2000/svg" class="mkvi-diagram" viewBox="0 0 240 160">
              <g text-anchor="middle" transform="translate(20 20)">
                <rect class="mkvi-node" x="10" y="10" width="140" height="80"/>
                <text id="short" class="mkvi-label" x="80" y="38">CRM</text>
                <text id="long" class="mkvi-note" x="80" y="66">Customer relationship</text>
                <text id="caption" class="mkvi-note" x="80" y="115">Across all processes</text>
              </g>
            </svg>
        """.trimIndent()
        val result = fitMarkViLabels(svg, css) { text, size, _ -> text.length * size / 2 }
        val fitted = Regex("""font-size:([\d.]+)px""").findAll(result).single().groupValues[1].toFloat()
        assertEquals(128f, fitted * "Customer relationship".length / 2, 0.01f)
        assertTrue(result.contains("Customer relationship"))
        assertTrue(result.contains("translate(20 20)"))
    }

    @Test
    fun leavesStandaloneAndAlreadyFittingDiagramsUnchanged() {
        val svg = """
            <svg class="mkvi-diagram">
              <rect class="mkvi-node" width="160" height="80"/>
              <text class="mkvi-label" x="16" y="36">CRM</text>
            </svg>
        """.trimIndent()
        assertEquals(svg, fitMarkViLabels(svg, css) { _, _, _ -> 50f })
        val standalone = svg.replace("class=\"mkvi-diagram\"", "")
        assertEquals(standalone, fitMarkViLabels(standalone, css) { _, _, _ -> 300f })
    }
}
