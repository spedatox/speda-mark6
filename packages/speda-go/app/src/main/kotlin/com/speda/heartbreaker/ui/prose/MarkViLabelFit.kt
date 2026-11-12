// SPDX-License-Identifier: AGPL-3.0-or-later
package com.speda.heartbreaker.ui.prose

import android.graphics.Paint
import android.graphics.Typeface
import java.io.StringReader
import java.io.StringWriter
import javax.xml.parsers.DocumentBuilderFactory
import javax.xml.transform.OutputKeys
import javax.xml.transform.TransformerFactory
import javax.xml.transform.dom.DOMSource
import javax.xml.transform.stream.StreamResult
import org.w3c.dom.Element
import org.xml.sax.InputSource

private val whitespace = Regex("\\s+")
private val nodeClasses = setOf("mkvi-node", "mkvi-decision", "mkvi-accept", "mkvi-reject")
private val positionedText = listOf("style", "transform", "dx", "dy", "rotate", "textLength")
private val typeSize = Regex("""\.mkvi-(label|note)\s*\{[^}]*font-size:\s*([\d.]+)px""")

private data class NodeBox(val left: Float, val top: Float, val width: Float, val height: Float) {
    val area get() = width * height
    fun contains(x: Float, y: Float) = x > left && x < left + width && y > top && y < top + height

    fun lineWidth(x: Float, anchor: String): Float {
        val start = x - left
        val end = width - start
        return when (anchor) {
            "middle" -> 2 * minOf(start, end) - 12
            "end" -> start - 6
            else -> end - 6
        }
    }
}

internal fun fitMarkViLabels(raw: String, css: String): String {
    val paint = Paint()
    val normal = Typeface.create("sans-serif", Typeface.NORMAL)
    val bold = Typeface.create("sans-serif", Typeface.BOLD)
    return fitMarkViLabels(raw, css) { text, size, isBold ->
        paint.typeface = if (isBold) bold else normal
        paint.textSize = size
        paint.measureText(text)
    }
}

/**
 * Fits simple, explicitly positioned lines inside themed sibling rectangles before
 * AndroidSVG renders them. The desktop widget does the same with browser font metrics.
 */
internal fun fitMarkViLabels(
    raw: String,
    css: String,
    measure: (text: String, size: Float, bold: Boolean) -> Float,
): String {
    if ("<!DOCTYPE" in raw) return raw
    val sizes = typeSize.findAll(css).associate { it.groupValues[1] to it.groupValues[2].toFloat() }
    val parser = DocumentBuilderFactory.newInstance().apply {
        isNamespaceAware = true
        isExpandEntityReferences = false
    }.newDocumentBuilder().apply {
        setEntityResolver { _, _ -> InputSource(StringReader("")) }
    }
    val document = parser.parse(InputSource(StringReader(raw)))
    val root = document.documentElement
    if (root.localName != "svg" || "mkvi-diagram" !in root.classes()) return raw

    var changed = false
    fun fitGroup(group: Element, inheritedAnchor: String) {
        val anchor = group.getAttribute("text-anchor").ifBlank { inheritedAnchor }
        val children = generateSequence(group.firstChild) { it.nextSibling }.filterIsInstance<Element>().toList()
        val boxes = children.mapNotNull { it.nodeBox() }
        for (text in children) {
            if (text.localName == "g") {
                fitGroup(text, anchor)
                continue
            }
            if (text.localName != "text" || positionedText.any(text::hasAttribute)) continue
            if (generateSequence(text.firstChild) { it.nextSibling }.any { it is Element }) continue
            val classes = text.classes()
            val role = when {
                "mkvi-note" in classes -> "note"
                "mkvi-label" in classes -> "label"
                else -> continue
            }
            val x = text.number("x") ?: continue
            val y = text.number("y") ?: continue
            val node = boxes.filter { it.contains(x, y) }.minByOrNull(NodeBox::area) ?: continue
            val room = node.lineWidth(x, text.getAttribute("text-anchor").ifBlank { anchor })
            val size = sizes[role] ?: continue
            val width = measure(text.textContent.trim().replace(whitespace, " "), size, role == "label")
            if (room > 0 && width > room) {
                text.setAttribute("style", "font-size:${size * room / width}px")
                changed = true
            }
        }
    }
    fitGroup(root, "start")
    if (!changed) return raw

    val output = StringWriter()
    TransformerFactory.newInstance().newTransformer().apply {
        setOutputProperty(OutputKeys.OMIT_XML_DECLARATION, "yes")
    }.transform(DOMSource(document), StreamResult(output))
    return output.toString()
}

private fun Element.classes() = getAttribute("class").split(whitespace)

private fun Element.number(name: String) = getAttribute(name).trim().removeSuffix("px")
    .toFloatOrNull()?.takeIf { it.isFinite() }

private fun Element.nodeBox(): NodeBox? {
    if (localName != "rect" || hasAttribute("transform") || classes().none { it in nodeClasses }) return null
    val width = number("width") ?: return null
    val height = number("height") ?: return null
    val left = if (hasAttribute("x")) number("x") ?: return null else 0f
    val top = if (hasAttribute("y")) number("y") ?: return null else 0f
    return NodeBox(left, top, width, height)
}
