// SPDX-License-Identifier: AGPL-3.0-or-later
package com.speda.heartbreaker.domain

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeArtifactTest {
    private val fixtures = Json.parseToJsonElement(
        checkNotNull(javaClass.getResourceAsStream("/native-artifacts.json")).bufferedReader().use { it.readText() },
    ).jsonObject

    @Test fun bothClientsUseTheSamePracticeAndInclusiveBoundaries() {
        val practice = parseNativeArtifact(fixtures["practice"].toString()) as ArtifactSpec.Practice
        assertTrue(artifactAnswerMatches(" 2\n", practice.items.single().answer))
        assertFalse(artifactAnswerMatches("A  B", "A B"))
        assertFalse(artifactAnswerMatches("a", "A"))
        val explore = parseNativeArtifact(fixtures["explore"].toString()) as ArtifactSpec.Explore
        fixtures.getValue("boundaries").jsonArray.forEach { row ->
            val (value, index) = row.jsonArray.map { it.jsonPrimitive.int }
            assertEquals(index, explore.activeCase(value))
        }
        assertNull(parseNativeArtifact(fixtures["explore"].toString().replace("\"through\":101", "\"through\":100")))
    }
}
