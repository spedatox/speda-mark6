// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.data

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioManager
import android.media.audiofx.Visualizer
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlin.math.sqrt

/**
 * How loud the agent is talking, right now — what the orb reacts to.
 *
 * ── Why this needs a microphone permission for playback ─────────────────────
 * Android exposes no "what is this app currently playing" meter. The only route
 * to the samples is [Visualizer], which taps an audio session's output — and
 * because that tap can technically be pointed at the mix, the platform gates it
 * behind RECORD_AUDIO. So an orb that genuinely moves with the voice costs a
 * microphone permission on an app that otherwise never records anything (its
 * dictation goes through the system recognizer precisely to avoid one).
 *
 * That trade is deliberate and it degrades cleanly: WITHOUT the permission this
 * class simply never emits, [level] stays at zero, and the orb falls back to its
 * own idle animation. Nothing else changes and nothing fails.
 *
 * ── One session, many players ───────────────────────────────────────────────
 * The session id is generated once and handed to every MediaPlayer the speaker
 * builds ([VoiceSpeaker] sets it before setDataSource, which is the only point
 * it can be set). That stability is the point: a Visualizer is bound to a
 * SESSION, not to a player, so one tap survives the whole conversation while
 * clips come and go underneath it. Re-attaching per sentence would mean tearing
 * down and rebuilding a hardware effect several times a sentence.
 */
class VoiceLevels(context: Context) {

    private val appContext = context.applicationContext

    /** The session every spoken clip plays into. Generated once, for the life of
     *  the process — see the class doc. */
    val sessionId: Int =
        (appContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager)
            .generateAudioSessionId()

    private val _level = MutableStateFlow(0f)

    /** 0..1, roughly perceptual. Zero whenever nothing is playing, and zero for
     *  the whole session when the permission was never granted. */
    val level: StateFlow<Float> = _level.asStateFlow()

    private var visualizer: Visualizer? = null

    fun hasPermission(): Boolean =
        ContextCompat.checkSelfPermission(appContext, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    /**
     * Start metering. Safe to call repeatedly — a second call with a live tap is
     * a no-op — and safe to call without the permission, where it does nothing.
     */
    fun start() {
        if (visualizer != null || !hasPermission()) return
        // Every step here can throw on a device that refuses the effect (some do,
        // and an emulator usually does). A missing meter must cost the orb its
        // reactivity, never the conversation.
        runCatching {
            val v = Visualizer(sessionId)
            // The smallest capture the device offers: this drives one number on
            // screen, and a larger window is samples fetched to be averaged away.
            v.captureSize = Visualizer.getCaptureSizeRange()[0]
            v.setDataCaptureListener(
                object : Visualizer.OnDataCaptureListener {
                    override fun onWaveFormDataCapture(
                        who: Visualizer?, waveform: ByteArray?, samplingRate: Int,
                    ) {
                        waveform ?: return
                        _level.value = rms(waveform)
                    }

                    override fun onFftDataCapture(
                        who: Visualizer?, fft: ByteArray?, samplingRate: Int,
                    ) = Unit
                },
                // Capture rate is in milliHertz. Cap at ~30Hz: the orb is drawn
                // at frame rate and cannot show more than that anyway, and the
                // callback runs on a binder thread we would rather not saturate.
                minOf(Visualizer.getMaxCaptureRate(), 30_000),
                true,  // waveform — the amplitude envelope is all this needs
                false, // no FFT; nothing here is drawn per frequency band
            )
            v.enabled = true
            visualizer = v
        }
    }

    /** Stop metering and drop the level, so a paused orb does not sit frozen at
     *  whatever the last sample happened to be. */
    fun stop() {
        val v = visualizer ?: run { _level.value = 0f; return }
        visualizer = null
        runCatching { v.enabled = false }
        runCatching { v.release() }
        _level.value = 0f
    }

    /**
     * RMS of one waveform window, normalised to 0..1.
     *
     * Visualizer hands back 8-bit UNSIGNED PCM with 128 as silence, which is why
     * this subtracts the midpoint rather than treating the bytes as signed — read
     * as signed, silence reads as a constant -128 and the orb sits at full
     * deflection doing nothing.
     *
     * The square root at the end is the perceptual part: RMS is linear in
     * pressure and hearing is not, so without it ordinary speech barely moves and
     * only a shout registers.
     */
    private fun rms(waveform: ByteArray): Float {
        if (waveform.isEmpty()) return 0f
        var sum = 0.0
        for (b in waveform) {
            val v = (b.toInt() and 0xFF) - 128
            sum += (v * v).toDouble()
        }
        val mean = sqrt(sum / waveform.size) / 128.0
        return sqrt(mean.coerceIn(0.0, 1.0)).toFloat()
    }
}
