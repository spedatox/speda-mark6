package com.speda.heartbreaker.domain

import com.speda.heartbreaker.data.AgentCommEntry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The feed is a room, not a ledger. These pin the two things that made it one:
 * a reply is its own message from its own agent, and the timeline is ordered by
 * when things actually happened.
 */
class CommTranscriptTest {

    private fun entry(
        id: Int,
        from: String,
        to: String,
        task: String = "t$id",
        result: String? = null,
        status: String = "ok",
        durationMs: Int? = 0,
        createdAt: String = "2026-08-10T12:00:00",
        kind: String = "dispatch",
        protocol: String = "direct",
    ) = AgentCommEntry(
        id = id, fromAgent = from, toAgent = to, task = task, result = result,
        status = status, durationMs = durationMs, createdAt = createdAt,
        kind = kind, protocol = protocol,
    )

    @Test
    fun `a dispatch and its reply are two messages from two agents`() {
        val msgs = CommTranscript.messages(
            listOf(entry(1, "speda", "centurion", task = "scan", result = "done", durationMs = 2000)),
        )

        assertEquals(2, msgs.size)
        assertEquals("speda", msgs[0].agent)
        assertEquals("scan", msgs[0].text)
        assertTrue("the task is the order going out", msgs[0].outbound)

        assertEquals("centurion", msgs[1].agent)
        assertEquals("done", msgs[1].text)
        assertFalse("the reply is work coming back", msgs[1].outbound)
    }

    @Test
    fun `the reply lands at created_at plus its duration`() {
        val start = CommTranscript.epoch("2026-08-10T12:00:00")
        val msgs = CommTranscript.messages(
            listOf(entry(1, "speda", "atomix", result = "ok", durationMs = 8_400)),
        )
        assertEquals(start, msgs[0].at)
        assertEquals(start + 8_400, msgs[1].at)
    }

    @Test
    fun `a running dispatch shows the target working instead of a gap`() {
        val msgs = CommTranscript.messages(
            listOf(entry(1, "speda", "atomix", result = null, status = "running", durationMs = null)),
        )
        assertEquals(2, msgs.size)
        assertTrue(msgs[1].running)
        assertEquals("atomix", msgs[1].agent)
        assertEquals("2026-08-10T12:00:00", msgs[1].since)
    }

    @Test
    fun `a failure still speaks, carrying its status`() {
        val msgs = CommTranscript.messages(
            listOf(entry(1, "speda", "ultron", result = null, status = "timeout", durationMs = 30_000)),
        )
        assertEquals(2, msgs.size)
        assertTrue(msgs[1].failed)
        assertEquals("timeout", msgs[1].status)
    }

    @Test
    fun `messages from two exchanges interleave by time, not by record`() {
        // A slow dispatch started first but answers last.
        val slow = entry(1, "speda", "centurion", task = "slow", result = "late", durationMs = 60_000)
        val quick = entry(2, "speda", "atomix", task = "quick", result = "early", durationMs = 1_000,
            createdAt = "2026-08-10T12:00:10")

        val order = CommTranscript.messages(listOf(slow, quick)).map { it.text }
        assertEquals(listOf("slow", "quick", "early", "late"), order)
    }

    @Test
    fun `consecutive lines from one agent group under a single head`() {
        // Two orders from SPEDA back to back (neither answered yet).
        val rows = CommTranscript.rows(
            listOf(
                entry(1, "speda", "centurion", task = "first", result = "", durationMs = 0),
                entry(2, "speda", "centurion", task = "second", result = "", durationMs = 0,
                    createdAt = "2026-08-10T12:00:01"),
            ),
        )
        assertEquals(2, rows.size)
        assertTrue("first line of the run carries the mark", rows[0].head)
        assertFalse("the continuation does not", rows[1].head)
    }

    @Test
    fun `a quiet stretch breaks the run and earns a clock`() {
        val rows = CommTranscript.rows(
            listOf(
                entry(1, "speda", "centurion", task = "before", result = "", durationMs = 0),
                entry(2, "speda", "centurion", task = "after", result = "", durationMs = 0,
                    createdAt = "2026-08-10T12:30:00"),
            ),
        )
        assertTrue("first row always shows the clock", rows[0].chip)
        assertTrue("a 30-minute gap shows it again", rows[1].chip)
        assertTrue("and starts a fresh run", rows[1].head)
    }
}
