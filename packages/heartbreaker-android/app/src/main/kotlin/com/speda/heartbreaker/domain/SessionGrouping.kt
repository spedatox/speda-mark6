package com.speda.heartbreaker.domain

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeParseException

/**
 * Time-grouped session list — literal port of groupSessions in Sidebar.tsx.
 * Buckets: Today / Yesterday / This week / This month / Older, empty groups
 * dropped, order preserved.
 */
data class SessionGroup(val label: String, val items: List<Session>)

private val LABELS = listOf("Today", "Yesterday", "This week", "This month", "Older")

fun groupSessions(sessions: List<Session>, zone: ZoneId = ZoneId.systemDefault()): List<SessionGroup> {
    val today = java.time.LocalDate.now(zone).atStartOfDay(zone)
    val yesterday = today.minusDays(1)
    val week = today.minusDays(7)
    val month = today.minusDays(30)

    val buckets = LABELS.associateWith { mutableListOf<Session>() }
    for (s in sessions) {
        val started = parseStartedAt(s.startedAt, zone)
        val label = when {
            started == null -> "Older"
            !started.isBefore(today) -> "Today"
            !started.isBefore(yesterday) -> "Yesterday"
            !started.isBefore(week) -> "This week"
            !started.isBefore(month) -> "This month"
            else -> "Older"
        }
        buckets.getValue(label).add(s)
    }
    return LABELS.mapNotNull { l ->
        val items = buckets.getValue(l)
        if (items.isEmpty()) null else SessionGroup(l, items)
    }
}

/** The backend sends naive ISO timestamps (UTC); tolerate both with/without zone. */
private fun parseStartedAt(raw: String, zone: ZoneId): java.time.ZonedDateTime? = try {
    val normalized = if (raw.endsWith("Z") || raw.contains('+') || raw.matches(Regex(".*-\\d{2}:\\d{2}$"))) raw else "${raw}Z"
    Instant.parse(normalized).atZone(zone)
} catch (_: DateTimeParseException) {
    null
} catch (_: Exception) {
    null
}
