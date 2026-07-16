package com.speda.heartbreaker.domain

import kotlinx.collections.immutable.PersistentList

/**
 * Segment builder — interleave tools into the text at the point they fired.
 * Literal port of buildSegments in Message.tsx. A message stores flat text + a
 * tool list stamped with afterChars; this turns that into an ordered sequence of
 * text / tools segments so tool activity shows AT the point it happened.
 * [revealedLen] clips to how far the typewriter has revealed, so tools unlock
 * progressively in step with the text reaching them. Tools sharing the exact
 * same afterChars group into one feed block.
 */
sealed interface Segment {
    data class Text(val text: String) : Segment
    data class Tools(val tools: List<ToolBadge>) : Segment
}

fun buildSegments(fullText: String, tools: List<ToolBadge>, revealedLen: Int): List<Segment> {
    val visible = tools
        .filter { (it.afterChars ?: 0) <= revealedLen }
        .sortedBy { it.afterChars ?: 0 }

    val segments = ArrayList<Segment>()
    var cursor = 0
    var i = 0
    while (i < visible.size) {
        val pos = minOf(visible[i].afterChars ?: 0, revealedLen)
        if (pos > cursor) {
            segments.add(Segment.Text(fullText.substring(cursor, pos)))
            cursor = pos
        }
        val group = ArrayList<ToolBadge>()
        while (i < visible.size && minOf(visible[i].afterChars ?: 0, revealedLen) == pos) {
            group.add(visible[i])
            i++
        }
        segments.add(Segment.Tools(group))
    }
    if (cursor < revealedLen) {
        segments.add(Segment.Text(fullText.substring(cursor, revealedLen)))
    }
    return segments
}

/** Convenience overload for the PersistentList held on [ChatMessage]. */
fun buildSegments(fullText: String, tools: PersistentList<ToolBadge>, revealedLen: Int): List<Segment> =
    buildSegments(fullText, tools as List<ToolBadge>, revealedLen)
