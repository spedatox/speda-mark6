package com.speda.heartbreaker.domain

import com.speda.heartbreaker.data.MessageJson
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toPersistentList
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class RoomParityTest {
    private val json = Json { ignoreUnknownKeys = true }
    private fun trace(): List<RoomEvent> = json.decodeFromString(javaClass.classLoader!!.getResource("fixtures/room_v1.json")!!.readText())
    private fun begin() = ChatState(activeSessionId = 1, isStreaming = true, messages = persistentListOf(
        ChatMessage("local-user", Role.User, "Plan the week"), ChatMessage("placeholder", Role.Assistant, "", isStreaming = true, sessionId = 1)))
    private fun play(state: ChatState) = trace().fold(state) { s, e -> reduce(s, ChatAction.RoomFrame(e, "placeholder", 1)) }

    @Test fun operation_wait_resume_and_replay_preserve_the_original_message() {
        fun event(sequence: Long, kind: String, text: String? = null) = RoomEvent("room", sequence,
            stepId = "operation", speaker = "orion", kind = kind,
            payload = buildJsonObject { if (text != null) put("text", text) })
        val events = listOf(event(1, "speaker_started"), event(2, "delta", "Running."),
            event(3, "step_waiting"), event(4, "operation_finished"), event(5, "step_resumed"), event(6, "delta", " Done."))
        val waiting = events.take(3).fold(begin()) { state, frame -> reduce(state, ChatAction.RoomFrame(frame, "placeholder", 1)) }
        assertFalse(waiting.isStreaming)
        assertEquals("Command running", waiting.messages.last().status)
        val finished = events.drop(3).fold(waiting) { state, frame -> reduce(state, ChatAction.RoomFrame(frame, "placeholder", 1)) }
        assertEquals("Running. Done.", finished.messages.last().content)
        assertTrue(finished.isStreaming)
        assertEquals(finished.messages, events.fold(finished) { state, frame -> reduce(state, ChatAction.RoomFrame(frame, "placeholder", 1)) }.messages)
    }

    @Test fun shared_desktop_trace_keeps_peer_exchange_and_reply_ids_without_owner_relay() {
        val result = play(begin())
        val speakers = result.messages.filter { it.speaker != null }
        assertEquals(listOf("speda", "ultron", "speda", "ultron"), speakers.map { it.speaker })
        assertEquals(listOf("Bringing Ultron in.", "@SPEDA, when is the midterm?",
            "The midterm is Friday.", "Then review on Thursday."), speakers.map { it.content })
        assertEquals("collaborate", speakers.first().tools.single().name)
        assertTrue(speakers.last().tools.isEmpty())
        assertEquals(9L, speakers[1].roomSequence)
        assertEquals(14L, speakers.last().roomSequence)
        assertEquals(listOf("speda", "ultron"), result.room!!.members)
        assertEquals(1, result.messages.count { it.roomNotice != null })
        assertEquals(1, result.messages.count { it.role == Role.User })
        assertEquals(5, historyMessageCount(result.messages))
        assertTrue(result.isStreaming) // Contributions do not finish the root reply.
        assertFalse(reduce(result, ChatAction.FinishRoomTurn(1)).isStreaming)
    }

    @Test fun full_replay_and_stale_starts_do_not_duplicate_or_erase_history() {
        val once = play(begin())
        val twice = play(once)
        assertEquals(once.messages, twice.messages)
        val cached = MessageJson.parseArray(MessageJson.encodeArray(once.messages))
        val restored = play(once.copy(messages = cached.toPersistentList()))
        assertEquals(once.messages.map { it.content }, restored.messages.map { it.content })
        assertEquals(once.messages.map { it.coordinationStepId }, restored.messages.map { it.coordinationStepId })
        assertEquals(once.messages.map { it.roomSequence }, restored.messages.map { it.roomSequence })
    }

    @Test fun private_consultation_never_creates_a_public_message() {
        val e = RoomEvent("room", 1, stepId = "private", speaker = "orion", kind = "speaker_started", payload = buildJsonObject { put("mode", "consult") })
        val state = reduce(begin(), ChatAction.RoomFrame(e, "placeholder", 1))
        assertEquals(begin().messages, state.messages)
        assertEquals(state, reduce(state, ChatAction.RoomFrame(e.copy(sequence = 2, kind = "delta", payload = buildJsonObject { put("text", "PRIVATE") }), "placeholder", 1)))
    }

    @Test fun late_room_and_worker_responses_cannot_switch_conversations() {
        val state = begin().copy(activeSessionId = 2)
        assertEquals(state, reduce(state, ChatAction.RoomFrame(trace().first(), "placeholder", 1)))
        assertEquals(state, reduce(state, ChatAction.SetRoom(RoomInfo("r", lead = "speda", members = listOf("speda"), version = 1, sequence = 0), 1)))
        assertEquals(state, reduce(state, ChatAction.FinishRoomTurn(1)))
        assertEquals(state, reduce(state, ChatAction.WorkerSnapshot(1, "s", null, SubagentRun("task"), true)))
    }

    @Test fun membership_versions_never_regress() {
        val newer = RoomInfo("room", lead = "ultron", members = listOf("speda", "ultron"), version = 4, sequence = 10)
        val state = begin().copy(room = newer)
        val result = play(state)
        assertEquals(newer, result.room)
    }

    @Test fun background_worker_survives_reply_completion_and_merges_into_loaded_parent() {
        val run = SubagentRun("worker", executionId = "worker", lastSequence = 20, running = true)
        var state = reduce(begin(), ChatAction.WorkerSnapshot(1, "step", null, run, true))
        state = reduce(state, ChatAction.FinishRoomTurn(1))
        assertFalse(state.isStreaming)
        assertTrue(state.messages.last().subagents!!.single().running)
        val history = ChatMessage("5", Role.Assistant, "Reply complete", coordinationStepId = "step", serverMessageId = 5,
            subagents = persistentListOf(run.copy(lastSequence = 10, running = false)))
        state = reduce(state, ChatAction.SelectSession(1, listOf(history)))
        assertEquals(1, state.messages.size)
        assertFalse(state.messages.single().isWorkerAnchor)
        assertEquals(20L, state.messages.single().subagents!!.single().lastSequence)
        assertFalse(state.isStreaming)
    }

    @Test fun worker_preview_has_bounded_steps_text_and_replay_ids() {
        val run = boundedWorkerRun(SubagentRun("w", prompt = "p".repeat(5000), report = "r".repeat(20000),
            steps = (1..200).map { SubagentStep("text", text = "a".repeat(1000)) }.toPersistentList(),
            seenEventIds = (1..500).map { "$it" }.toPersistentList()))
        assertTrue(run.steps.sumOf { it.text!!.length } <= 32000)
        assertTrue(run.steps.size <= 100)
        assertEquals(128, run.seenEventIds.size)
        assertEquals(4000, run.prompt!!.length)
        assertEquals(16000, run.report!!.length)
        assertTrue(run.activityPartial)
    }

    @Test fun stale_history_cannot_erase_a_live_room_contribution_or_finish_the_root_reply() {
        var state = begin()
        trace().take(3).forEach { state = reduce(state, ChatAction.RoomFrame(it, "placeholder", 1)) }
        val stale = ChatMessage("coord:lead", Role.Assistant, "Older", coordinationStepId = "lead", roomSequence = 2)
        val result = reduce(state, ChatAction.SelectSession(1, listOf(stale)))
        assertEquals("Bringing Ultron in.", result.messages.single().content)
        assertTrue(result.messages.single().isStreaming)
        assertTrue(result.isStreaming)
    }

    @Test fun reconnect_placeholder_is_removed_even_when_history_is_newer_than_replayed_start() {
        val complete = play(begin())
        val attaching = complete.copy(messages = complete.messages.add(ChatMessage("reattach", Role.Assistant, "", isStreaming = true, sessionId = 1)))
        val result = reduce(attaching, ChatAction.RoomFrame(trace()[1], "reattach", 1))
        assertFalse(result.messages.any { it.id == "reattach" })
        assertEquals(complete.messages, result.messages)
    }
}
