package com.speda.heartbreaker.domain

/**
 * `table-layout: auto`, in the HTML4 formulation the browsers implement — the one
 * thing the Compose table renderer (ui/prose/Prose.kt) cannot get for free and
 * whose absence made every cell a fixed-width box. Pure arithmetic, so it is
 * tested on the JVM rather than eyeballed on a device.
 */
object TableColumns {

    /**
     * Width for each column given its min-content ([minW]) and max-content
     * ([maxW]) width in pixels, fitted to [target].
     *
     * Every column sits between the two, and whatever space is left over is
     * shared out in proportion to how much each column asked for. Only when the
     * columns cannot even reach min-content does the total exceed [target] — that
     * is the one case the table's horizontal scroll exists for.
     */
    fun solve(minW: IntArray, maxW: IntArray, target: Int): IntArray {
        val cols = minW.size
        val out = IntArray(cols)
        if (cols == 0) return out
        val sumMin = minW.sum()
        val sumMax = maxW.sum()
        when {
            // Everything fits on one line — grow to fill the width (`width: 100%`).
            sumMax <= target -> {
                val slack = target - sumMax
                for (c in 0 until cols) {
                    out[c] = maxW[c] + if (sumMax == 0) slack / cols else slack * maxW[c] / sumMax
                }
            }
            // Wrapping, but everything still fits: hand out the slack in
            // proportion to each column's appetite for it.
            sumMin <= target -> {
                val slack = target - sumMin
                val span = sumMax - sumMin
                for (c in 0 until cols) {
                    out[c] = minW[c] + if (span == 0) slack / cols else slack * (maxW[c] - minW[c]) / span
                }
            }
            // Too narrow for the longest word in every column — overflow, and let
            // the caller scroll.
            else -> return minW.copyOf()
        }
        // Integer division leaves a few pixels on the table; give them to the
        // widest column so the grid still ends flush with its border.
        val widest = (0 until cols).maxByOrNull { out[it] } ?: 0
        out[widest] += target - out.sum()
        return out
    }
}
