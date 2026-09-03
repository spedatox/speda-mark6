// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.data

import android.media.AudioAttributes
import android.media.MediaDataSource
import android.media.MediaPlayer
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.SpeakableFilter
import com.speda.heartbreaker.domain.splitSentences
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlin.coroutines.resume

/**
 * One turn's worth of speech — the Kotlin half of heartbreaker `lib/voice.ts`.
 *
 * Construct when a spoken turn starts, [feed] it the reply as it streams, then
 * [finish] when the turn ends. [stop] cuts it off; the object is single-use
 * either way, so the next turn gets a new one.
 *
 * ── Why it is shaped like this ──────────────────────────────────────────────
 * Three constraints, and every design choice here falls out of one of them.
 *
 * DELTAS ARE NOT LINES AND LINES ARE NOT SENTENCES. Text arrives a few tokens
 * at a time. Whether a line is inside a ``` fence cannot be judged until the
 * line is complete, so deltas are held to the last newline before being filtered
 * ([SpeakableFilter]); and a sentence is not spoken until it is terminated,
 * because half an utterance is worse than a whole one a moment later.
 *
 * SYNTHESIS IS SLOWER THAN PLAYBACK AND MUST NOT BE SERIAL WITH IT. Sentence
 * N+1 is generated while N is still being heard — that overlap is the whole
 * reason a spoken reply starts promptly instead of after the last word has been
 * written. Two or three in flight is enough; more just buys audio for sentences
 * the owner may interrupt before reaching.
 *
 * ORDER SURVIVES CONCURRENCY. Sentence 3 finishing synthesis before sentence 2
 * must not let it speak first, so playback awaits each job IN SEQUENCE rather
 * than racing them. That is what the queue of Deferred is for.
 *
 * ── What this path is not ───────────────────────────────────────────────────
 * This is the per-sentence HTTP path (`/voice/speak`), which every engine
 * supports. The desktop also has a WebSocket path that keeps ONE prosodic
 * context across a whole turn, so intonation carries across a sentence
 * boundary the way a person's does; here every sentence is still a standalone
 * utterance with its own terminal contour. That seam is the cost of this path,
 * and it is the reason /voice/stream exists — worth porting later, not first.
 */
class VoiceSpeaker(
    private val api: IgorApi,
    private val config: AppConfig,
    private val agentId: String?,
    private val scope: CoroutineScope,
    /** The audio session every clip plays into, so one Visualizer can meter the
     *  whole conversation rather than being rebuilt per sentence. See
     *  [VoiceLevels]. */
    private val audioSessionId: Int,
    /** Called as the turn moves between silence, generating and speaking, so the
     *  surface can show which of the three is happening. */
    private val onState: (State) -> Unit = {},
) {
    enum class State { IDLE, THINKING, SPEAKING }

    /** How many sentences may be in synthesis at once. */
    private val inflight = Semaphore(MAX_INFLIGHT)

    /** The utterances of this turn, in order, each resolving to its audio (or to
     *  null where synthesis failed and the sentence is simply skipped). */
    private val queue = Channel<Deferred<ByteArray?>>(Channel.UNLIMITED)

    private val filter = SpeakableFilter()

    /** Text that has been filtered but not yet split into whole sentences. */
    private val buffered = StringBuilder()

    /** The stream's tail below the last newline — not yet judgeable, see feed. */
    private var partialLine = ""

    private var player: MediaPlayer? = null
    private var stopped = false
    private val drain: Job = scope.launch { playInOrder() }

    /**
     * Take the next piece of the reply.
     *
     * Only whole LINES are judged: a fence's opening ``` can arrive in one delta
     * and its content in the next, so a line held back is a line that cannot be
     * mistaken for prose and read aloud.
     */
    fun feed(delta: String) {
        if (stopped) return
        partialLine += delta
        val cut = partialLine.lastIndexOf('\n')
        if (cut < 0) return
        val complete = partialLine.substring(0, cut + 1)
        partialLine = partialLine.substring(cut + 1)
        absorb(filter.speakable(complete))
    }

    /**
     * The reply is finished. Flush the last line and the last part-sentence:
     * without this a reply that does not end in a newline loses its final
     * sentence, which is most of them.
     */
    fun finish() {
        if (stopped) return
        if (partialLine.isNotEmpty()) {
            absorb(filter.speakable(partialLine + "\n"))
            partialLine = ""
        }
        val tail = buffered.toString().trim()
        buffered.setLength(0)
        if (tail.isNotEmpty()) enqueue(tail)
        queue.close()
    }

    /** Cut playback and abandon whatever is still being synthesised. */
    fun stop() {
        if (stopped) return
        stopped = true
        queue.close()
        drain.cancel()
        releasePlayer()
        onState(State.IDLE)
    }

    private fun absorb(text: String) {
        buffered.append(text)
        val (sentences, rest) = splitSentences(buffered.toString())
        if (sentences.isEmpty()) return
        buffered.setLength(0)
        buffered.append(rest)
        sentences.forEach(::enqueue)
    }

    private fun enqueue(sentence: String) {
        val clean = sentence.trim()
        // A "sentence" of punctuation left over from a stripped artefact is not
        // worth a request, let alone the characters it would be billed for.
        if (clean.isEmpty() || clean.none { it.isLetterOrDigit() }) return
        onState(State.THINKING)
        val job = scope.async {
            inflight.withPermit { api.speak(config, clean, agentId) }
        }
        // The channel is unbounded, so this cannot suspend or fail here; a closed
        // channel (the turn was stopped) just drops the job.
        if (queue.trySend(job).isFailure) job.cancel()
    }

    /**
     * Play each clip to completion, in the order the sentences were written.
     *
     * Awaiting the job here rather than at enqueue time is the point: several
     * are generating at once, and this loop is what puts them back in order.
     */
    private suspend fun playInOrder() {
        try {
            for (job in queue) {
                val audio = try {
                    job.await()
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    null
                }
                if (stopped) return
                // A sentence that failed to synthesise is skipped, not retried
                // and not announced: the reply is on screen regardless, and a
                // spoken apology for a missing sentence is worse than the gap.
                if (audio == null || audio.isEmpty()) continue
                onState(State.SPEAKING)
                playClip(audio)
            }
        } finally {
            releasePlayer()
            if (!stopped) onState(State.IDLE)
        }
    }

    /** Play one MP3 and suspend until it ends. */
    private suspend fun playClip(audio: ByteArray) = suspendCancellableCoroutine { cont ->
        val mp = MediaPlayer()
        player = mp
        var settled = false
        // Resume EXACTLY once. Completion and error can both fire for one clip,
        // and a second resume on a continuation is a crash, not a no-op.
        val settle = {
            if (!settled) {
                settled = true
                runCatching { mp.reset(); mp.release() }
                if (player === mp) player = null
                if (cont.isActive) cont.resume(Unit)
            }
        }
        try {
            mp.setAudioAttributes(
                AudioAttributes.Builder()
                    // SPEECH/MEDIA rather than a notification stream: this is the
                    // agent talking, and it should duck music and follow the
                    // media volume the owner already set, not the ringer.
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            // Before setDataSource — the only point the platform lets a session
            // be assigned. After it, the setter throws.
            if (audioSessionId != 0) mp.audioSessionId = audioSessionId
            mp.setDataSource(ByteArrayMediaSource(audio))
            mp.setOnCompletionListener { settle() }
            mp.setOnErrorListener { _, _, _ -> settle(); true }
            mp.prepare()
            mp.start()
        } catch (e: Exception) {
            settle()
        }
        cont.invokeOnCancellation { settle() }
    }

    private fun releasePlayer() {
        val mp = player ?: return
        player = null
        runCatching { mp.stop() }
        runCatching { mp.reset(); mp.release() }
    }

    private companion object {
        const val MAX_INFLIGHT = 3
    }
}

/**
 * Plays MediaPlayer straight off a byte array.
 *
 * The alternative is a temp file per sentence, which for a spoken conversation
 * means a few hundred small writes an hour to the owner's storage, plus the
 * cleanup that gets forgotten when a turn is interrupted. The audio is already
 * in memory when it arrives; it may as well be played from there.
 */
private class ByteArrayMediaSource(private val bytes: ByteArray) : MediaDataSource() {
    override fun readAt(position: Long, buffer: ByteArray, offset: Int, size: Int): Int {
        if (position >= bytes.size) return -1
        val count = minOf(size.toLong(), bytes.size - position).toInt()
        System.arraycopy(bytes, position.toInt(), buffer, offset, count)
        return count
    }

    override fun getSize(): Long = bytes.size.toLong()
    override fun close() = Unit
}
