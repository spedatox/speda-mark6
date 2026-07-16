package com.speda.heartbreaker.domain

import kotlinx.collections.immutable.persistentListOf
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Reducer parity — the subtle behaviours from store/chat.ts that the port must
 * preserve. Hand-authored (chat.ts can't be imported under Node type-stripping
 * because it imports React), each case ties back to a specific TS rule.
 */
class ReducerTest {

    private fun assistant(id: String, content: String = "", streaming: Boolean = false, sessionId: Int? = null, status: String? = null) =
        ChatMessage(id = id, role = Role.Assistant, content = content, isStreaming = streaming, sessionId = sessionId, status = status)

    @Test
    fun appendChunk_clears_status() {
        val start = ChatState(messages = persistentListOf(assistant("a", streaming = true, status = "Thinking")))
        val next = reduce(start, ChatAction.AppendChunk("a", "hi"))
        assertEquals("hi", next.messages[0].content)
        assertNull(next.messages[0].status)
    }

    @Test
    fun selectSession_preserves_in_flight_tail_not_in_payload() {
        // A streaming bubble for session 7 that the loaded history doesn't include.
        val live = assistant("live", content = "partial", streaming = true, sessionId = 7)
        val start = ChatState(messages = persistentListOf(live), isStreaming = true)
        val loaded = listOf(ChatMessage(id = "h1", role = Role.User, content = "q"))

        val next = reduce(start, ChatAction.SelectSession(7, loaded))

        assertEquals(listOf("h1", "live"), next.messages.map { it.id })
        assertTrue("kept tail keeps streaming flag set", next.isStreaming)
    }

    @Test
    fun selectSession_replaces_when_nothing_to_keep() {
        val start = ChatState(messages = persistentListOf(assistant("old")), isStreaming = false)
        val loaded = listOf(ChatMessage(id = "h1", role = Role.User, content = "q"))
        val next = reduce(start, ChatAction.SelectSession(3, loaded))
        assertEquals(listOf("h1"), next.messages.map { it.id })
        assertFalse(next.isStreaming)
        assertEquals(3, next.activeSessionId)
    }

    @Test
    fun finishMessage_ignored_when_absent() {
        // User switched away — the streaming bubble is gone from view.
        val start = ChatState(messages = persistentListOf(assistant("other")), isStreaming = true, activeSessionId = 1)
        val next = reduce(start, ChatAction.FinishMessage("gone", sessionId = 9))
        assertEquals(start, next) // untouched — no yank back, no flag flip
    }

    @Test
    fun errorMessage_keeps_streamed_content_and_adds_banner() {
        val start = ChatState(
            messages = persistentListOf(assistant("a", content = "half an answer", streaming = true)),
            isStreaming = true,
        )
        val next = reduce(start, ChatAction.ErrorMessage("a", "host restarted"))
        val m = next.messages[0]
        assertEquals("half an answer", m.content) // never vaporized
        assertTrue(m.isError)
        assertEquals("host restarted", m.errorNote)
        assertFalse(m.isStreaming)
        assertFalse(next.isStreaming)
    }

    @Test
    fun addTool_then_setToolResult() {
        val start = ChatState(messages = persistentListOf(assistant("a", streaming = true)))
        val withTool = reduce(start, ChatAction.AddTool("a", ToolBadge(id = "t1", name = "web_search", afterChars = 0)))
        assertEquals(1, withTool.messages[0].tools.size)
        val withResult = reduce(withTool, ChatAction.SetToolResult("a", "t1", "5 hits"))
        assertEquals("5 hits", withResult.messages[0].tools[0].result)
    }

    @Test
    fun truncateFrom_slices_inclusive_of_index() {
        val start = ChatState(
            messages = persistentListOf(
                ChatMessage(id = "u", role = Role.User, content = "q"),
                assistant("a", content = "answer"),
            ),
        )
        val next = reduce(start, ChatAction.TruncateFrom("a"))
        assertEquals(listOf("u"), next.messages.map { it.id })
    }

    @Test
    fun deleteSession_active_clears_view() {
        val start = ChatState(
            sessions = persistentListOf(Session(1, "A", ""), Session(2, "B", "")),
            activeSessionId = 2,
            messages = persistentListOf(assistant("a")),
        )
        val next = reduce(start, ChatAction.DeleteSession(2))
        assertEquals(listOf(1), next.sessions.map { it.id })
        assertNull(next.activeSessionId)
        assertTrue(next.messages.isEmpty())
    }
}
