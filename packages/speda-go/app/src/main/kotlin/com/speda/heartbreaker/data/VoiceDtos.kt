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
