package com.speda.heartbreaker.domain

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import java.time.LocalDate

/**
 * The ```calendar fence contract from CalendarBlock.tsx:
 *
 *   { title?, range?, days: [ { date: "yyyy-mm-dd", label?,
 *       events?: [ { time?, end?, title, location?, color? } ] } ] }
 */
data class CalEvent(
    val title: String,
    val time: String? = null,
    val end: String? = null,
    val location: String? = null,
    val color: String? = null,
)

data class CalDay(
    val date: String,
    val label: String? = null,
    val events: List<CalEvent> = emptyList(),
) {
    /** yyyy-mm-dd parsed as a LOCAL date (the web is careful about this too). */
    val localDate: LocalDate? = runCatching {
        DATE_RE.find(date.trim())?.let {
            LocalDate.of(it.groupValues[1].toInt(), it.groupValues[2].toInt(), it.groupValues[3].toInt())
        }
    }.getOrNull()

    private companion object {
        val DATE_RE = Regex("^(\\d{4})-(\\d{2})-(\\d{2})")
    }
}

data class CalendarSpec(val title: String?, val range: String?, val days: List<CalDay>)

private val LenientJson = Json { ignoreUnknownKeys = true; isLenient = true }

/** Null when not valid JSON, or when `days` isn't an array (as the web requires). */
fun parseCalendarSpec(raw: String): CalendarSpec? = runCatching {
    val o = LenientJson.parseToJsonElement(raw) as? JsonObject ?: return null
    val daysArr = o["days"] as? JsonArray ?: return null // Array.isArray(s.days)
    val days = daysArr.mapNotNull { el ->
        val d = el as? JsonObject ?: return@mapNotNull null
        val date = d["date"].str() ?: return@mapNotNull null
        CalDay(
            date = date,
            label = d["label"].str(),
            events = (d["events"] as? JsonArray).orEmpty().mapNotNull { ev ->
                val e = ev as? JsonObject ?: return@mapNotNull null
                val title = e["title"].str() ?: return@mapNotNull null
                CalEvent(
                    title = title,
                    time = e["time"].str(),
                    end = e["end"].str(),
                    location = e["location"].str(),
                    color = e["color"].str(),
                )
            },
        )
    }
    CalendarSpec(title = o["title"].str(), range = o["range"].str(), days = days)
}.getOrNull()

private fun kotlinx.serialization.json.JsonElement?.str(): String? {
    val p = this as? JsonPrimitive ?: return null
    return if (p is JsonNull) null else p.content
}

private fun JsonArray?.orEmpty(): JsonArray = this ?: JsonArray(emptyList())
