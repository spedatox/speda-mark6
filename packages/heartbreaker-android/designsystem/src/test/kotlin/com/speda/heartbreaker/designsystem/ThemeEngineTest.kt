package com.speda.heartbreaker.designsystem

import com.speda.heartbreaker.designsystem.theme.ThemeEngine
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Logic-parity gate (plan §7 #1). Asserts the Kotlin [ThemeEngine] reproduces
 * theme.ts `buildThemeVars` / `deriveAccents` BYTE-FOR-BYTE for every brand
 * accent + the war-room amber, against fixtures dumped from the shipping TS by
 * packages/heartbreaker/scripts/gen-theme-fixtures.ts.
 *
 * If this fails after a theme.ts change, regenerate the fixture and reconcile —
 * do not loosen the assertion.
 */
class ThemeEngineTest {

    private data class Fixtures(
        val accents: Map<String, String>,
        val themeVars: Map<String, Map<String, String>>,
        val accentFamily: Map<String, Triple<String, String, String>>,
    )

    private fun loadFixtures(): Fixtures {
        val stream = javaClass.classLoader?.getResourceAsStream("fixtures/theme_vars.json")
            ?: error("theme_vars.json missing — run gen-theme-fixtures.ts")
        val root = Json.parseToJsonElement(stream.readBytes().decodeToString()).jsonObject

        val accents = root.getValue("accents").jsonObject
            .mapValues { it.value.jsonPrimitive.content }

        val themeVars = root.getValue("themeVars").jsonObject.mapValues { (_, agentVars) ->
            agentVars.jsonObject.mapValues { it.value.jsonPrimitive.content }
        }

        val accentFamily = root.getValue("accentFamily").jsonObject.mapValues { (_, fam) ->
            val o = fam.jsonObject
            Triple(
                o.getValue("accent").jsonPrimitive.content,
                o.getValue("bright").jsonPrimitive.content,
                o.getValue("dim").jsonPrimitive.content,
            )
        }
        return Fixtures(accents, themeVars, accentFamily)
    }

    @Test
    fun buildThemeVars_matches_typescript_for_every_agent() {
        val fx = loadFixtures()
        assertTrue("expected 9 agents in fixtures", fx.themeVars.size == 9)

        for ((agent, accent) in fx.accents) {
            val expected = fx.themeVars.getValue(agent)
            val actual = ThemeEngine.buildThemeVars(accent)

            // Same key set.
            assertEquals("[$agent] token keys differ", expected.keys, actual.keys)
            // Same value for every token — the byte-for-byte parity check.
            for ((key, expectedValue) in expected) {
                assertEquals("[$agent] $key mismatch", expectedValue, actual.getValue(key))
            }
        }
    }

    @Test
    fun deriveAccents_matches_typescript() {
        val fx = loadFixtures()
        for ((agent, accent) in fx.accents) {
            val (eAccent, eBright, eDim) = fx.accentFamily.getValue(agent)
            val a = ThemeEngine.deriveAccents(accent)
            assertEquals("[$agent] accent", eAccent, a.accent)
            assertEquals("[$agent] bright", eBright, a.bright)
            assertEquals("[$agent] dim", eDim, a.dim)
        }
    }

    @Test
    fun buildPalette_resolves_without_throwing_for_every_agent() {
        val fx = loadFixtures()
        for ((_, accent) in fx.accents) {
            val palette = ThemeEngine.buildPalette(accent)
            // Accent family carries the exact brand hue (alpha 1).
            assertEquals(1f, palette.accent.alpha)
        }
    }
}
