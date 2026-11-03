package com.speda.heartbreaker.domain

import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.VoiceSpeaker
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import okhttp3.OkHttpClient
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class RoomVoiceQueueTest {
    @Test fun next_participant_waits_for_previous_playback_and_stop_cancels_the_wait() = runTest {
        val client = OkHttpClient()
        val previous = CompletableDeferred<Unit>()
        val queued = VoiceSpeaker(IgorApi(client, client), AppConfig("http://unused", "test", "speda"), "ultron", backgroundScope, 0,
            waitForPlayback = { previous.await() })
        queued.finish()
        val waiting = async { queued.awaitPlayback() }
        runCurrent()
        assertFalse(waiting.isCompleted)
        previous.complete(Unit)
        runCurrent()
        assertTrue(waiting.isCompleted)

        val gate = CompletableDeferred<Unit>()
        val stopped = VoiceSpeaker(IgorApi(client, client), AppConfig("http://unused", "test", "speda"), "orion", backgroundScope, 0,
            waitForPlayback = { gate.await() })
        stopped.finish()
        val cancelled = async { stopped.awaitPlayback() }
        runCurrent()
        stopped.stop()
        runCurrent()
        assertTrue(cancelled.isCompleted)
        assertFalse(gate.isCompleted)
    }
}
