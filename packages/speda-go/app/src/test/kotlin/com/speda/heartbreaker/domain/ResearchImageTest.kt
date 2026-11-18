// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.domain

import org.commonmark.parser.Parser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ResearchImageTest {
    private val imageUrl = "https://archive.example/telescope.jpg"
    private val sourceUrl = "https://archive.example/record"

    @Test
    fun linkedMarkdownImageKeepsItsDescriptionAndSource() {
        val paragraph = Parser.builder().build().parse("Evidence [![A telescope]($imageUrl)]($sourceUrl) found.").firstChild
        assertNull(markdownImage(paragraph.firstChild))
        assertEquals(SourceImage(imageUrl, "A telescope", sourceUrl), markdownImage(paragraph.firstChild.next))
    }

    @Test
    fun imagePanelsAcceptExistingCaptionsAndWaitForCompleteFences() {
        assertEquals(SourceImage(imageUrl, "A telescope"), parseImageFence("$imageUrl\nA telescope"))
        val body = "$imageUrl\nA telescope\nSource: $sourceUrl"
        val partial = "```image | Telescope\n$body"
        assertTrue(splitBoardPanels(partial).isEmpty())
        assertTrue(MarkdownPrep.prepare(partial).isEmpty())
        val completed = splitBoardPanels("$partial\n```").single()
        assertEquals(SourceImage(imageUrl, "A telescope", sourceUrl), parseImageFence(completed.body))
    }
}
