// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Wire DTOs for the Voices settings surface — mirrors of the interfaces in
 * lib/api.ts used by VoicesTab.tsx. The same four ElevenLabs parameters that
 * engine's own Studio settings panel exposes; Azure and OpenAI ignore them.
 */
@Serializable
data class VoiceSettings(
    val stability: Float? = null,
    @SerialName("similarity_boost") val similarityBoost: Float? = null,
    val style: Float? = null,
    val speed: Float? = null,
    @SerialName("use_speaker_boost") val useSpeakerBoost: Boolean? = null,
)

/** GET /voice/agents — one dispatch-target agent's voice pin + tuning. */
@Serializable
data class VoiceAgentInfo(
    @SerialName("agent_id") val agentId: String,
    val name: String = "",
    val domain: String = "",
    /** The profile's own default (or the engine default if the profile has none). */
    @SerialName("default_voice") val defaultVoice: String = "",
    /** The owner's pin, or null if this agent still uses the profile default. */
    @SerialName("voice_id") val voiceId: String? = null,
    /** What will actually speak right now — voiceId if set, else defaultVoice. */
    @SerialName("effective_voice") val effectiveVoice: String = "",
    @SerialName("voice_settings") val voiceSettings: VoiceSettings? = null,
)

/** One voice from GET /voice/voices — spans every configured engine. */
@Serializable
data class VoiceOption(
    val id: String,          // full "provider:model:voice" ref
    val name: String = "",
    val provider: String = "",   // azure | openai | elevenlabs
    val model: String = "",
    val locale: String = "",
    val gender: String = "",
    val display: String = "",
)

/**
 * The board half of GET /voice/status — how many windows a turn may open, how
 * long to stagger their arrival, how deep the caption runs.
 *
 * Read from the backend rather than held as constants here for one reason: the
 * SAME settings shape what the agent is asked to WRITE (the word budgets and the
 * window ceiling reach it through the voice brief). A client with its own idea of
 * the ceiling would eventually draw six windows for a reply briefed to stage ten.
 */
@Serializable
data class CanvasSettings(
    val enabled: Boolean = true,
    @SerialName("max_panels") val maxPanels: Int = 10,
    @SerialName("reveal_stagger_ms") val revealStaggerMs: Int = 160,
    @SerialName("caption_lines") val captionLines: Int = 3,
)
