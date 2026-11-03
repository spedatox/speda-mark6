package com.speda.heartbreaker.domain

import com.speda.heartbreaker.data.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.test.*
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class SessionRecoveryTest {
    private val id = "a".repeat(32)
    private val step = "b".repeat(32)
    private fun card(sequence: Long, running: Boolean = true) = WorkerCard(step, "10",
        SubagentRun(id, executionId = id, agent = "worker", label = "Research", lastSequence = sequence, running = running))
    private fun event(sequence: Long, phase: String = "text") = SseEvent("subagent", buildJsonObject {
        put("id", id); put("execution_id", id); put("parent_step_id", step); put("sequence", sequence)
        put("phase", phase); put("text", "Progress"); put("status", if (phase == "finished") "succeeded" else "running")
    })

    @Test fun disconnect_recovers_snapshot_then_replays_without_starting_new_work() = runTest {
        val seen = mutableListOf<WorkerCard>()
        val cursors = mutableListOf<Long>()
        var snapshots = 0
        val job = backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) {
            watchSessionWorkers({
                snapshots++
                WorkerSessionSnapshot(1, if (snapshots == 1) 10 else 12, 0, listOf(card(if (snapshots == 1) 10 else 12)))
            }, { cursor -> cursors.add(cursor); flow {
                if (cursors.size == 1) { emit(event(11)); throw IOException("lost socket") }
                emit(event(12)) // Safe overlap with the snapshot.
                emit(event(13, "finished"))
                awaitCancellation()
            } }, { true }, seen::add, {})
        }
        advanceTimeBy(1001); runCurrent()
        assertEquals(listOf(10L, 12L), cursors)
        assertEquals(listOf(10L, 11L, 12L, 13L), seen.map { it.run.lastSequence })
        assertFalse(seen.last().run.running)
        job.cancelAndJoin()
        val calls = snapshots
        advanceTimeBy(60000)
        assertEquals(calls, snapshots)
    }

    @Test fun terminal_permission_failure_is_visible_and_never_retried() = runTest {
        var attempts = 0
        var notice: String? = null
        watchSessionWorkers({ attempts++; throw ApiFailure("Unauthorized", false) }, { flow {} }, { true }, {}, { notice = it })
        advanceTimeBy(60000)
        assertEquals(1, attempts)
        assertEquals("Unauthorized", notice)
    }

    @Test fun old_snapshot_after_view_switch_cannot_publish_cards() = runTest {
        var current = true
        val seen = mutableListOf<WorkerCard>()
        watchSessionWorkers({ current = false; WorkerSessionSnapshot(1, 5, 0, listOf(card(5))) }, { error("Must not attach") },
            { current }, seen::add, {})
        assertTrue(seen.isEmpty())
    }

    @Test fun malformed_worker_identity_stops_recovery() = runTest {
        var notice: String? = null
        watchSessionWorkers({ WorkerSessionSnapshot(1, 0, 0, emptyList()) }, { flow { emit(SseEvent("subagent", buildJsonObject { put("id", "wrong") })) } },
            { true }, {}, { notice = it })
        assertTrue(notice!!.contains("invalid execution identity"))
    }

    @Test fun multiline_sse_and_comments_preserve_the_envelope() {
        val decoder = SseDecoder()
        assertNull(decoder.line(": keepalive"))
        assertNull(decoder.line("data: {\"type\":\"chunk\","))
        assertNull(decoder.line("data: \"data\":\"Merhaba 👋\"}"))
        assertEquals("Merhaba 👋", (decoder.line("")!!.data as JsonPrimitive).content)
        assertNull(decoder.finish())
    }

    @Test fun malformed_and_oversized_sse_frames_are_explicit_failures() {
        assertThrows(ApiFailure::class.java) { SseDecoder().apply { line("data: broken") }.line("") }
        assertThrows(ApiFailure::class.java) { SseDecoder().line("data: " + "x".repeat(256001)) }
    }

    @Test fun snapshots_reject_wrong_sessions_or_unbounded_cards() {
        assertThrows(ApiFailure::class.java) { parseWorkerSnapshot(Json.parseToJsonElement("{\"session_id\":2,\"sequence\":0,\"cards\":[]}"), 1) }
        val huge = buildJsonObject { put("session_id", 1); put("sequence", 0); put("cards", JsonArray(List(65) { JsonNull })) }
        assertThrows(ApiFailure::class.java) { parseWorkerSnapshot(huge, 1) }
    }
}
