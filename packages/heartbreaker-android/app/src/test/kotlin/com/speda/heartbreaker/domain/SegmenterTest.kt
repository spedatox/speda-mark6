package com.speda.heartbreaker.domain

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Logic-parity gate for [buildSegments] (plan §7). Asserts the Kotlin port
 * reproduces the interleaving/grouping of the VERBATIM buildSegments copy in
 * packages/heartbreaker/scripts/gen-chat-fixtures.ts, whose output is dumped to
 * segments.json.
 */
class SegmenterTest {

    @Test
    fun buildSegments_matches_typescript_cases() {
        val stream = javaClass.classLoader?.getResourceAsStream("fixtures/segments.json")
            ?: error("segments.json missing — run gen-chat-fixtures.ts")
        val cases = Json.parseToJsonElement(stream.readBytes().decodeToString()).jsonArray

        for (case in cases) {
            val o = case.jsonObject
            val name = o.getValue("name").jsonPrimitive.content
            val fullText = o.getValue("fullText").jsonPrimitive.content
            val revealedLen = o.getValue("revealedLen").jsonPrimitive.int
            val tools = o.getValue("tools").jsonArray.map { t ->
                val to = t.jsonObject
                ToolBadge(
                    id = to.getValue("id").jsonPrimitive.content,
                    name = "x",
                    afterChars = to["afterChars"]?.jsonPrimitive?.int,
                )
            }

            val actual = buildSegments(fullText, tools, revealedLen)
            val expected = o.getValue("segments").jsonArray

            assertEquals("[$name] segment count", expected.size, actual.size)
            expected.forEachIndexed { i, exp ->
                val eo = exp.jsonObject
                when (eo.getValue("kind").jsonPrimitive.content) {
                    "text" -> {
                        val seg = actual[i] as Segment.Text
                        assertEquals("[$name] text[$i]", eo.getValue("text").jsonPrimitive.content, seg.text)
                    }
                    "tools" -> {
                        val seg = actual[i] as Segment.Tools
                        val ids = eo.getValue("toolIds").jsonArray.map { it.jsonPrimitive.content }
                        assertEquals("[$name] toolIds[$i]", ids, seg.tools.map { it.id })
                    }
                }
            }
        }
    }
}
