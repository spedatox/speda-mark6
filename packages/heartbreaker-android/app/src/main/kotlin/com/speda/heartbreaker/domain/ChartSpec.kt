package com.speda.heartbreaker.domain

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull

/**
 * The ```chart fence contract from ChartBlock.tsx.
 *
 *   line/area/bar: { type, title?, xKey, series:[{key,label?,color?}], data:[{...}],
 *                    unit?, yDomain?, height? }
 *   pie:           { type:"pie", title?, data:[{label,value,color?}], height? }
 *
 * Parsed leniently by hand rather than with @Serializable: `data` rows are
 * heterogeneous (string x-values, numeric series) and a half-written spec from a
 * model must fail softly, not throw.
 */
data class ChartSeries(val key: String, val label: String? = null, val color: String? = null)

data class ChartSpec(
    val type: String,
    val title: String? = null,
    val xKey: String = "x",
    val series: List<ChartSeries> = emptyList(),
    /** Row values are String or Float; `null` for anything unusable. */
    val data: List<Map<String, Any?>> = emptyList(),
    val unit: String? = null,
    val yDomain: List<Float>? = null,
    val height: Int? = null,
)

private val LenientJson = Json { ignoreUnknownKeys = true; isLenient = true }

/** Returns null when the fence isn't valid JSON (still streaming, or malformed). */
fun parseChartSpec(raw: String): ChartSpec? = runCatching {
    val o = LenientJson.parseToJsonElement(raw) as? JsonObject ?: return null
    val type = (o["type"] as? JsonPrimitive)?.contentOrEmpty() ?: return null

    val series = (o["series"] as? JsonArray).orEmpty().mapNotNull { el ->
        val s = el as? JsonObject ?: return@mapNotNull null
        val key = (s["key"] as? JsonPrimitive)?.contentOrEmpty() ?: return@mapNotNull null
        ChartSeries(
            key = key,
            label = (s["label"] as? JsonPrimitive)?.contentOrEmpty(),
            color = (s["color"] as? JsonPrimitive)?.contentOrEmpty(),
        )
    }

    val data = (o["data"] as? JsonArray).orEmpty().mapNotNull { el ->
        (el as? JsonObject)?.mapValues { (_, v) -> jsonScalar(v) }
    }

    val yDomain = (o["yDomain"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.doubleOrNull?.toFloat() }
        ?.takeIf { it.size == 2 }

    ChartSpec(
        type = type.lowercase(),
        title = (o["title"] as? JsonPrimitive)?.contentOrEmpty(),
        xKey = (o["xKey"] as? JsonPrimitive)?.contentOrEmpty() ?: "x",
        series = series,
        data = data,
        unit = (o["unit"] as? JsonPrimitive)?.contentOrEmpty(),
        yDomain = yDomain,
        height = (o["height"] as? JsonPrimitive)?.intOrNull,
    )
}.getOrNull()

/** Numbers stay numbers (series values); everything else becomes its string form. */
private fun jsonScalar(v: kotlinx.serialization.json.JsonElement): Any? {
    val p = v as? JsonPrimitive ?: return null
    p.doubleOrNull?.let { return it.toFloat() }
    p.booleanOrNull?.let { return it }
    return p.contentOrEmpty()
}

private fun JsonPrimitive.contentOrEmpty(): String? = if (this is kotlinx.serialization.json.JsonNull) null else content

private fun JsonArray?.orEmpty(): JsonArray = this ?: JsonArray(emptyList())
