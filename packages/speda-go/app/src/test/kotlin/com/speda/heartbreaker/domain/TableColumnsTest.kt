// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The auto-layout solver behind the prose table. Every case asserts the invariant
 * that actually shows up on screen: the row ends flush with the table's border
 * (widths sum to the target), and no column is ever cut below the longest word it
 * has to print.
 */
class TableColumnsTest {

    private fun solve(min: IntArray, max: IntArray, target: Int) = TableColumns.solve(min, max, target)

    @Test
    fun grows_to_fill_when_everything_fits_on_one_line() {
        val w = solve(intArrayOf(20, 20, 20), intArrayOf(100, 200, 100), 800)
        assertEquals("fills the width", 800, w.sum())
        // The slack is shared in proportion to max-content, so the middle column
        // — which asked for twice as much — stays twice as wide.
        assertTrue("proportional", w[1] > w[0] && w[1] > w[2])
        assertEquals("symmetric columns stay equal", w[0], w[2])
    }

    @Test
    fun shares_the_slack_when_the_table_has_to_wrap() {
        // A notes column with a lot to say next to two narrow ones.
        val min = intArrayOf(60, 50, 70)
        val max = intArrayOf(120, 60, 900)
        val w = solve(min, max, 400)
        assertEquals(400, w.sum())
        for (c in min.indices) {
            assertTrue("column $c never below min-content", w[c] >= min[c])
            assertTrue("column $c never above max-content", w[c] <= max[c])
        }
        assertTrue("the hungry column takes most of the slack", w[2] > w[0] + w[1])
    }

    @Test
    fun falls_back_to_min_content_and_overflows_when_too_narrow() {
        val min = intArrayOf(120, 120, 120)
        val w = solve(min, intArrayOf(300, 300, 300), 200)
        assertEquals("min-content verbatim", listOf(120, 120, 120), w.toList())
        assertTrue("overflows — the caller scrolls", w.sum() > 200)
    }

    @Test
    fun a_single_column_takes_the_whole_width() {
        assertEquals(listOf(500), solve(intArrayOf(40), intArrayOf(90), 500).toList())
    }

    @Test
    fun survives_degenerate_input() {
        assertEquals(0, solve(IntArray(0), IntArray(0), 500).size)
        // Empty cells all round: no division by zero, still flush.
        assertEquals(500, solve(intArrayOf(0, 0), intArrayOf(0, 0), 500).sum())
        // Nothing to distribute — an exact fit is left exactly alone.
        assertEquals(listOf(100, 300), solve(intArrayOf(50, 50), intArrayOf(100, 300), 400).toList())
    }
}
