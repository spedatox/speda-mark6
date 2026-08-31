// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

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

    /* ── stitchTables — Android-only, no TS counterpart to gate against ───── */

    @Test
    fun reattaches_a_row_stranded_behind_a_blank_line() {
        val src = """
            | Item | Amount |
            |---|---|
            | Rent | 8,000 |

            | Total | | 24,245.83 | |
        """.trimIndent()
        assertEquals(
            """
            | Item | Amount |
            |---|---|
            | Rent | 8,000 |
            | Total | | 24,245.83 | |
            """.trimIndent(),
            MarkdownPrep.stitchTables(src),
        )
    }

    @Test
    fun leaves_a_second_table_alone() {
        val src = """
            | A | B |
            |---|---|
            | 1 | 2 |

            | C | D |
            |---|---|
            | 3 | 4 |
        """.trimIndent()
        assertEquals(src, MarkdownPrep.stitchTables(src))
    }

    @Test
    fun leaves_prose_and_fenced_pipes_alone() {
        val src = """
            | A | B |
            |---|---|
            | 1 | 2 |

            Some prose after the table.

            ```
            | not | a | table |
            ```

            | still | prose |
        """.trimIndent()
        assertEquals(src, MarkdownPrep.stitchTables(src))
    }

    @Test
    fun ignores_documents_without_tables() {
        val src = "# Heading\n\nA paragraph with a | pipe in it.\n"
        assertEquals(src, MarkdownPrep.stitchTables(src))
    }
}
