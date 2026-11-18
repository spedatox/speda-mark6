// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.domain

import java.net.URI
import org.commonmark.node.Image
import org.commonmark.node.Link
import org.commonmark.node.Node
import org.commonmark.node.Text

data class SourceImage(val url: String, val caption: String, val source: String? = null)

fun isImageUrl(value: String): Boolean = runCatching {
    val uri = URI(value.trim())
    uri.scheme?.lowercase() in setOf("http", "https") && uri.host != null && uri.userInfo == null
}.getOrDefault(false)

private val SOURCE_LINE = Regex("^Source:\\s*(.*)$", RegexOption.IGNORE_CASE)

fun parseImageFence(body: String): SourceImage {
    val rows = boardRows(body)
    val source = rows.lastOrNull()?.let { SOURCE_LINE.matchEntire(it)?.groupValues?.get(1) }
        ?.takeIf(::isImageUrl)
    val caption = (if (source == null) rows else rows.dropLast(1)).drop(1).joinToString(" ")
    return SourceImage(rows.firstOrNull().orEmpty(), caption, source)
}

private fun altText(node: Node): String = when (node) {
    is Text -> node.literal
    else -> generateSequence(node.firstChild) { it.next }.joinToString("") { altText(it) }
}

fun markdownImage(node: Node): SourceImage? = when (node) {
    is Image -> SourceImage(node.destination, altText(node))
    is Link -> (node.firstChild as? Image)?.takeIf { it.next == null }?.let {
        SourceImage(it.destination, altText(it), node.destination.takeIf(::isImageUrl))
    }
    else -> null
}
