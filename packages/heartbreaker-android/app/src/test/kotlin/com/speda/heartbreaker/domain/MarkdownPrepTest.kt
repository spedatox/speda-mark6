package com.speda.heartbreaker.domain

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Logic-parity gate for the markdown pre-processors (plan §7). These are all
 * regex — exactly where a JS→Kotlin port drifts silently (lookbehind, split with
 * capture groups, replacement escaping), so every case is asserted against output
 * dumped from the verbatim TS in gen-chat-fixtures.ts.
 */
class MarkdownPrepTest {

    private fun cases() = Json
        .parseToJsonElement(
            javaClass.classLoader?.getResourceAsStream("fixtures/markdown_prep.json")
                ?.readBytes()?.decodeToString()
                ?: error("markdown_prep.json missing — run gen-chat-fixtures.ts"),
        ).jsonArray

    @Test
    fun matches_typescript_for_every_case() {
        val cases = cases()
        assertTrue("expected fixtures", cases.isNotEmpty())
        for (c in cases) {
            val o = c.jsonObject
            val name = o.getValue("name").jsonPrimitive.content
            val input = o.getValue("input").jsonPrimitive.content

            assertEquals(
                "[$name] normalizeCodeFences",
                o.getValue("normalizeCodeFences").jsonPrimitive.content,
                MarkdownPrep.normalizeCodeFences(input),
            )
            assertEquals(
                "[$name] prepareMath",
                o.getValue("prepareMath").jsonPrimitive.content,
                MarkdownPrep.prepareMath(input),
            )
            assertEquals(
                "[$name] sanitizePartialMarkdown",
                o.getValue("sanitizePartialMarkdown").jsonPrimitive.content,
                MarkdownPrep.sanitizePartialMarkdown(input),
            )
            assertEquals(
                "[$name] prepare (full pipeline)",
                o.getValue("prepare").jsonPrimitive.content,
                MarkdownPrep.prepare(input),
            )
        }
    }
}
