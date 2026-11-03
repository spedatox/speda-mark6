package com.speda.heartbreaker.domain

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.*
import org.junit.Test

class SubagentEventsTest {
    private fun event(json: String) = Json.parseToJsonElement(json) as JsonObject

    @Test
    fun durableReplayDoesNotDuplicateTextOrToolsOrResurrectFinishedWork() {
        var run = foldSubagentEvent(null, event("""{"id":"child","execution_id":"child","sequence":1,"event_id":"one","phase":"started","agent":"autobot","role":"autobot","owner_agent":"optimus","source":"forge"}"""))
        val text = event("""{"id":"child","sequence":2,"event_id":"two","phase":"text","text":"Evidence"}""")
        run = foldSubagentEvent(run, text)
        assertSame(run, foldSubagentEvent(run, text))
        run = foldSubagentEvent(run, event("""{"id":"child","sequence":3,"phase":"tool","tool_call_id":"call","tool":"read_file"}"""))
        run = foldSubagentEvent(run, event("""{"id":"child","sequence":4,"phase":"tool_result","tool_call_id":"call","result":"Confirmed"}"""))
        run = foldSubagentEvent(run, event("""{"id":"child","sequence":5,"phase":"tool","tool_call_id":"call","tool":"read_file"}"""))
        assertEquals(2, run.steps.size)
        assertEquals("Confirmed", run.steps[1].result)
        run = foldSubagentEvent(run, event("""{"id":"child","sequence":6,"phase":"finished","status":"succeeded","ok":true}"""))
        assertSame(run, foldSubagentEvent(run, event("""{"id":"child","sequence":1,"phase":"started"}""")))
        assertFalse(run.running)
        assertEquals("child", run.executionId)
        assertEquals("Autobot · supervised by Optimus", workerLabel(run))
    }

    @Test
    fun eventIdentityWithoutSequenceAndUncertainOutcomesRemainTruthful() {
        var run = foldSubagentEvent(null, event("""{"id":"child","event_id":"one","phase":"text","text":"Once"}"""))
        assertSame(run, foldSubagentEvent(run, event("""{"id":"child","event_id":"one","phase":"text","text":"Once"}""")))
        run = foldSubagentEvent(run, event("""{"id":"child","event_id":"two","phase":"finished","status":"reconciling","role":"decepticon","owner_agent":"scourge"}"""))
        assertFalse(run.running)
        assertEquals(false, run.ok)
        assertEquals("Decepticon · supervised by Scourge", workerLabel(run))
    }

    @Test
    fun persistedWorkerMetadataAndStepsSurviveHistoryAndOfflineCache() {
        val payload = event("""{"id":"message","role":"assistant","content":"Parent reply","tools":[],"subagents":[{"id":"child","execution_id":"child","agent":"autobot","role":"autobot","owner_agent":"optimus","source":"forge","status":"succeeded","running":false,"lastSequence":9,"steps":[{"kind":"tool","tool":"read_file","toolCallId":"call","result":"Recorded evidence"}]}]}""")
        val message = com.speda.heartbreaker.data.MessageJson.parseMessage(payload)
        val restored = com.speda.heartbreaker.data.MessageJson.parseArray(com.speda.heartbreaker.data.MessageJson.encodeArray(listOf(message))).single()
        val run = restored.subagents!!.single()
        assertEquals("child", run.executionId)
        assertEquals("optimus", run.ownerAgent)
        assertEquals(9L, run.lastSequence)
        assertEquals("Recorded evidence", run.steps.single().result)
        assertFalse(run.running)
    }

    @Test
    fun lostExecutorHasUnknownOutcomeAndStopsTheRunningIndicator() {
        var run = foldSubagentEvent(null, event("""{"id":"child","phase":"started","status":"running"}"""))
        run = foldSubagentEvent(run, event("""{"id":"child","phase":"executor_unavailable","status":"unknown"}"""))
        assertFalse(run.running)
        assertEquals("unknown", run.status)
        assertNotEquals(true, run.ok)
    }
}
