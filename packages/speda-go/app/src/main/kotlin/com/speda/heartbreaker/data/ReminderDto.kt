package com.speda.heartbreaker.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Wire types for Settings ▸ Reminders — the standing reminders Igor asks and
 * keeps asking until answered (app/models/reminder_definition.py).
 *
 * These are DEFINITIONS, not runs. A reminder an agent opens itself (Atomix's
 * evening checklist, composed fresh each night) has no definition and so never
 * appears in the list — only in the history.
 */

@Serializable
data class ReminderOption(val label: String = "", val value: String = "")

@Serializable
data class ReminderDefinition(
    val id: String = "",
    val agent: String = "speda",
    val text: String = "",
    /** "HH:MM" wall clock in the owner's timezone; empty = whenever the tick runs. */
    val at: String = "",
    /** "*" or weekday numbers, 1=Mon … 7=Sun. */
    val days: String = "*",
    val options: List<ReminderOption> = emptyList(),
    @SerialName("every_minutes") val everyMinutes: Int = 5,
    @SerialName("max_asks") val maxAsks: Int = 10,
    val enabled: Boolean = true,
    @SerialName("updated_at") val updatedAt: String = "",
)

/** PUT body — the id travels in the path, so it is not repeated here. */
@Serializable
data class ReminderDefinitionBody(
    val text: String,
    val agent: String,
    val at: String,
    val days: String,
    val options: List<ReminderOption>,
    @SerialName("every_minutes") val everyMinutes: Int,
    @SerialName("max_asks") val maxAsks: Int,
    val enabled: Boolean,
) {
    companion object {
        fun from(d: ReminderDefinition) = ReminderDefinitionBody(
            text = d.text, agent = d.agent, at = d.at, days = d.days,
            options = d.options, everyMinutes = d.everyMinutes,
            maxAsks = d.maxAsks, enabled = d.enabled,
        )
    }
}

@Serializable
data class ReminderDefinitionsResponse(val definitions: List<ReminderDefinition> = emptyList())

/** One closed cycle — what was actually answered or missed. */
@Serializable
data class ReminderCycleInfo(
    @SerialName("reminder_id") val reminderId: String = "",
    val day: String = "",
    val status: String = "",
    val answer: String = "",
    val via: String = "",
    val asks: Int = 0,
    @SerialName("closed_at") val closedAt: String = "",
)

@Serializable
data class ReminderHistoryResponse(val history: List<ReminderCycleInfo> = emptyList())
