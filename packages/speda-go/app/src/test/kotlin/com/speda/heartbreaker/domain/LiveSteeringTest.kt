package com.speda.heartbreaker.domain

import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class LiveSteeringTest {
    private fun target() = SteeringTarget(AppConfig("http://server", "owner", "speda"), "request", 1, "step", 1)

    @Test fun unknown_delivery_reuses_input_id_and_never_changes_expected_step() = runTest {
        val submissions = mutableListOf<Pair<SteeringTarget, String>>()
        val accepted = mutableListOf<String>()
        val controller = LiveSteering(backgroundScope, { target() }, { true }, { source, _, id ->
            submissions.add(source to id)
            if (submissions.size == 1) throw IOException("lost acknowledgment")
            true
        }, { _, _, id -> accepted.add(id) }, {})
        assertFalse(controller.submit("Use November"))
        assertTrue(controller.submit("Use November"))
        assertEquals(submissions[0], submissions[1])
        assertEquals(listOf(submissions[0].second), accepted)
        assertEquals("step", submissions[1].first.stepId)
    }

    @Test fun simultaneous_identical_sends_share_one_request_and_one_echo() = runTest {
        val ack = CompletableDeferred<Boolean>()
        var sends = 0
        var echoes = 0
        val controller = LiveSteering(backgroundScope, { target() }, { true }, { _, _, _ -> sends++; ack.await() }, { _, _, _ -> echoes++ }, {})
        val first = async { controller.submit("Steer") }
        val second = async { controller.submit("Steer") }
        runCurrent()
        assertEquals(1, sends)
        ack.complete(true)
        assertTrue(first.await()); assertTrue(second.await())
        assertEquals(1, echoes)
    }

    @Test fun late_acknowledgment_after_switching_away_and_back_never_echoes_into_new_view() = runTest {
        val ack = CompletableDeferred<Boolean>()
        var current = target()
        var echoes = 0
        val controller = LiveSteering(backgroundScope, { current }, { it.view == current.view }, { _, _, _ -> ack.await() }, { _, _, _ -> echoes++ }, {})
        val request = async { controller.submit("Original instruction") }
        runCurrent()
        current = current.copy(view = 3)
        ack.complete(true)
        assertTrue(request.await())
        assertEquals(0, echoes)
    }

    @Test fun rejected_steering_remains_unconfirmed_without_an_optimistic_echo() = runTest {
        var echo = false
        var notice: String? = null
        val controller = LiveSteering(backgroundScope, { target() }, { true }, { _, _, _ -> false }, { _, _, _ -> echo = true }, { notice = it })
        assertFalse(controller.submit("Draft"))
        assertFalse(echo)
        assertTrue(notice!!.contains("draft is retained"))
    }
}
