// SPDX-License-Identifier: AGPL-3.0-or-later
package com.speda.heartbreaker.domain

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull

sealed interface ArtifactSpec {
    val description: String

    data class Choice(val options: List<String>, val answerIndex: Int)
    data class Exercise(val prompt: String, val code: String?, val answer: String, val explanation: String, val choice: Choice?)
    data class Practice(override val description: String, val items: List<Exercise>) : ArtifactSpec
    data class Parameter(val label: String, val min: Int, val max: Int, val initial: Int, val unit: String?)
    data class Outcome(val through: Int, val label: String, val detail: String)
    data class Explore(override val description: String, val parameter: Parameter, val cases: List<Outcome>) : ArtifactSpec {
        fun activeCase(value: Int) = cases.indexOfFirst { value <= it.through }
    }
}

private fun JsonObject.text(key: String, limit: Int): String? =
    (get(key) as? JsonPrimitive)?.takeIf { it.isString }?.content
        ?.takeIf { it.length <= limit && it.isNotBlank() }

private fun JsonObject.integer(key: String): Int? =
    (get(key) as? JsonPrimitive)?.takeUnless { it.isString }?.doubleOrNull
        ?.takeIf { it in -10000.0..10000.0 && it % 1.0 == 0.0 }?.toInt()

fun parseNativeArtifact(raw: String): ArtifactSpec? {
    if (raw.length > 24000) return null
    val root = runCatching { Json.parseToJsonElement(raw) }.getOrNull() as? JsonObject ?: return null
    val description = root.text("description", 320) ?: return null
    return when (root.text("kind", 12)) {
        "practice" -> {
            val items = (root["items"] as? JsonArray)?.takeIf { it.size in 1..8 } ?: return null
            ArtifactSpec.Practice(description, items.map { element ->
                val item = element as? JsonObject ?: return null
                val code = if ("code" in item) item.text("code", 4000) ?: return null else null
                val choice = when (item.text("type", 12)) {
                    "choice" -> {
                        val options = (item["options"] as? JsonArray)?.takeIf { it.size in 2..6 } ?: return null
                        val labels = options.map { option ->
                            (option as? JsonPrimitive)?.takeIf { it.isString }?.content
                                ?.takeIf { it.isNotBlank() && it.length <= 180 } ?: return null
                        }
                        if (labels.map { it.trim() }.distinct().size != labels.size) return null
                        val correct = item.integer("answerIndex")?.takeIf { it in labels.indices } ?: return null
                        ArtifactSpec.Choice(labels, correct)
                    }
                    "text" -> null
                    null -> if ("type" !in item) null else return null
                    else -> return null
                }
                ArtifactSpec.Exercise(
                    item.text("prompt", 320) ?: return null, code,
                    choice?.let { it.options[it.answerIndex] } ?: item.text("answer", 240) ?: return null,
                    item.text("explanation", 1000) ?: return null, choice,
                )
            })
        }
        "explore" -> {
            val parameter = root["parameter"] as? JsonObject ?: return null
            val min = parameter.integer("min") ?: return null
            val max = parameter.integer("max") ?: return null
            val initial = parameter.integer("initial") ?: return null
            if (min >= max || initial !in min..max) return null
            val unit = if ("unit" in parameter) parameter.text("unit", 20) ?: return null else null
            val label = parameter.text("label", 100) ?: return null
            val cases = (root["cases"] as? JsonArray)?.takeIf { it.size in 1..12 } ?: return null
            var previous = min - 1
            val outcomes = cases.map { element ->
                val step = element as? JsonObject ?: return null
                val through = step.integer("through") ?: return null
                if (through <= previous || through > max) return null
                previous = through
                ArtifactSpec.Outcome(through, step.text("label", 100) ?: return null,
                    step.text("detail", 600) ?: return null)
            }
            if (previous != max) return null
            ArtifactSpec.Explore(description, ArtifactSpec.Parameter(label, min, max, initial, unit), outcomes)
        }
        else -> null
    }
}

fun artifactAnswerMatches(answer: String, expected: String): Boolean =
    answer.replace("\r\n", "\n").trim() == expected.replace("\r\n", "\n").trim()
