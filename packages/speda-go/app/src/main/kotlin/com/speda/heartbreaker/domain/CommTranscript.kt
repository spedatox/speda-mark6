package com.speda.heartbreaker.domain

import com.speda.heartbreaker.data.AgentCommEntry
import java.time.Instant

/**
 * The inter-agent feed as a GROUP CHAT rather than a transaction log.
 *
 * A dispatch record carries BOTH halves of an exchange, which is right for an
 * audit table and wrong for a room: the reply belongs to the agent that wrote
 * it, at the moment it arrived — not folded inside the message that asked for
 * it. Nesting it there made the roster read as a request log, and put the
 * answer somewhere the eye does not look for one.
 *
 * So one record becomes up to two messages: the task from `fromAgent` at
 * `createdAt`, and the reply from `toAgent` at `createdAt + durationMs` (the
 * only arrival time the record gives us). A dispatch still running contributes
 * a live placeholder instead, so the room shows someone working rather than a
 * gap.
 *
 * Mirror of `commMessages()` in the desktop client's CommBubble.tsx.
 */
object CommTranscript {

    private val FAILED = setOf("error", "timeout", "offline", "refused")

    /** A quiet stretch earns a separator, the way any chat client marks one. */
    const val GAP_MS = 5 * 60 * 1000L

    /** One line in the room: an agent said something at a moment in time. */
    data class Msg(
        val key: String,
        val agent: String,
        val text: String,
        /** Epoch millis — what the timeline sorts on. */
        val at: Long,
        /** A task precedes its own reply when both land on the same millisecond. */
        val seq: Int,
        /** An order going out (tinted) vs work coming back (neutral). */
        val outbound: Boolean,
        val running: Boolean = false,
        /** Dispatch start, for the live elapsed counter. */
        val since: String? = null,
        val failed: Boolean = false,
        val status: String? = null,
        val durationMs: Int? = null,
        val party: Boolean = false,
        val broadcast: Boolean = false,
        val copyText: String = "",
    )

    /** A rendered row: the message plus how it sits against the one before it. */
    data class Row(
        val msg: Msg,
        /** First of this agent's run — carries the mark, the name and the tail. */
        val head: Boolean,
        /** The room went quiet before this line; show the clock. */
        val chip: Boolean,
    )

    fun epoch(iso: String): Long {
        val withZone = if (iso.endsWith("Z") || iso.contains("+")) iso else "${iso}Z"
        return runCatching { Instant.parse(withZone).toEpochMilli() }.getOrElse { 0L }
    }

    /** Flatten dispatch records into a single chronological transcript. */
    fun messages(entries: List<AgentCommEntry>): List<Msg> {
        val out = ArrayList<Msg>(entries.size * 2)
        for (e in entries) {
            val started = epoch(e.createdAt)
            val result = e.result.orEmpty()
            val failed = e.status in FAILED

            out += Msg(
                key = "${e.id}:task",
                agent = e.fromAgent,
                text = e.task,
                at = started,
                seq = 0,
                outbound = true,
                party = e.protocol == "house_party",
                broadcast = e.kind == "broadcast",
                copyText = if (result.isNotEmpty()) {
                    "${e.task}\n\n--- ${e.toAgent.uppercase()} ---\n$result"
                } else {
                    e.task
                },
            )

            when {
                e.status == "running" -> out += Msg(
                    key = "${e.id}:working",
                    agent = e.toAgent,
                    text = "",
                    at = started,
                    seq = 1,
                    outbound = false,
                    running = true,
                    since = e.createdAt,
                )
                result.isNotEmpty() || failed -> out += Msg(
                    key = "${e.id}:reply",
                    agent = e.toAgent,
                    text = result.ifEmpty { e.status },
                    at = started + (e.durationMs ?: 0),
                    seq = 1,
                    outbound = false,
                    failed = failed,
                    status = e.status,
                    durationMs = e.durationMs,
                    copyText = result.ifEmpty { e.status },
                )
            }
        }
        return out.sortedWith(compareBy({ it.at }, { it.seq }, { it.key }))
    }

    /**
     * Group consecutive messages from one agent and mark where the room went
     * quiet, so a long scrollback reads as a conversation with pauses in it
     * rather than one undifferentiated column.
     */
    fun rows(entries: List<AgentCommEntry>): List<Row> {
        var prevAgent = ""
        var prevAt = 0L
        return messages(entries).map { m ->
            val gap = prevAt > 0L && m.at - prevAt > GAP_MS
            val head = gap || m.agent != prevAgent
            val chip = prevAt == 0L || gap
            prevAgent = m.agent
            prevAt = m.at
            Row(m, head = head, chip = chip)
        }
    }
}
