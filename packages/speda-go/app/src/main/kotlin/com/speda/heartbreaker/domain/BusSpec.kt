// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.domain

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull

/**
 * The ```bus fence contract from prompts/core/06_visual_output.md — a static
 * snapshot of the EGO "Otobüs Nerede?" arrivals board for one stop number,
 * from `bus_arrivals` (app/services/transit.py). Unlike [AircraftSpec], this
 * is never re-polled after the fence renders: a bus board is stale within
 * seconds regardless of how often it's refreshed, so the card just draws the
 * snapshot it was given.
 *
 *   { stopNumber, entries: [ { line, route, live, eta?, speedKmh?, plate?,
 *       stopIndex?, totalStops?, tags?, nextDeparture?, inWords? } ] }
 */
data class BusEntry(
    val line: String,
    val route: String,
    val live: Boolean,
    // live=true fields
    val eta: String? = null,
    val speedKmh: Int? = null,
    val plate: String? = null,
    val stopIndex: Int? = null,
    val totalStops: Int? = null,
    val tags: List<String> = emptyList(),
    // live=false fields
    val nextDeparture: String? = null,
    val inWords: String? = null,
)

data class BusStopSpec(val stopNumber: String, val entries: List<BusEntry>)

private val LenientJson = Json { ignoreUnknownKeys = true; isLenient = true }

/** Null when not valid JSON, missing `stopNumber`, or `entries` isn't an array. */
fun parseBusSpec(raw: String): BusStopSpec? = runCatching {
    val o = LenientJson.parseToJsonElement(raw) as? JsonObject ?: return null
    val stopNumber = o.str("stopNumber")?.takeIf { it.isNotBlank() } ?: return null
    val entriesArr = o["entries"] as? JsonArray ?: return null
    val entries = entriesArr.mapNotNull { el ->
        val e = el as? JsonObject ?: return@mapNotNull null
        val line = e.str("line")?.takeIf { it.isNotBlank() } ?: return@mapNotNull null
        val route = e.str("route") ?: ""
        BusEntry(
            line = line,
            route = route,
            live = e.boolv("live") ?: false,
            eta = e.str("eta"),
            speedKmh = e.intv("speedKmh"),
            plate = e.str("plate"),
            stopIndex = e.intv("stopIndex"),
            totalStops = e.intv("totalStops"),
            tags = (e["tags"] as? JsonArray).orEmptyArr().mapNotNull { t -> (t as? JsonPrimitive)?.content },
            nextDeparture = e.str("nextDeparture"),
            inWords = e.str("inWords"),
        )
    }
    BusStopSpec(stopNumber = stopNumber, entries = entries).takeIf { it.entries.isNotEmpty() }
}.getOrNull()

/* ── JsonObject scalar helpers (null-soft) ───────────────────────────────────── */

private fun JsonObject.prim(key: String): JsonPrimitive? =
    (this[key] as? JsonPrimitive)?.takeIf { it !is kotlinx.serialization.json.JsonNull }

private fun JsonObject.str(key: String): String? = prim(key)?.content
private fun JsonObject.intv(key: String): Int? = prim(key)?.intOrNull
private fun JsonObject.boolv(key: String): Boolean? = prim(key)?.booleanOrNull
private fun JsonArray?.orEmptyArr(): JsonArray = this ?: JsonArray(emptyList())
