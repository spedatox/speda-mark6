package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.VoiceAgentInfo
import com.speda.heartbreaker.data.VoiceOption
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.launch

/**
 * Voices — per-agent voice pin + ElevenLabs tuning. Mobile port of
 * VoicesTab.tsx: a row per dispatch-target agent, tap Edit to replace the
 * list with that agent's voice picker and the four ElevenLabs sliders plus
 * the speaker-boost toggle.
 *
 * The PIN (falls back to the profile's own default voice) and the TUNING
 * (falls back to that voice's own ElevenLabs dashboard settings) clear
 * independently — "Reset to default" clears both at once for a clean slate.
 */
@Composable
fun VoicesTab(config: AppConfig, graph: AppGraph) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val a = t.settingsVoices
    val scope = rememberCoroutineScope()
    val api = graph.api

    var agents by remember { mutableStateOf<List<VoiceAgentInfo>>(emptyList()) }
    var options by remember { mutableStateOf<List<VoiceOption>>(emptyList()) }
    var editing by remember { mutableStateOf<String?>(null) }

    suspend fun reload() {
        agents = api.fetchVoiceAgents(config)
        options = api.fetchVoiceOptions(config)
    }
    LaunchedEffect(config) { reload() }

    val current = agents.firstOrNull { it.agentId == editing }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        if (editing == null || current == null) {
            HbText(a.blurb, style = HbType.readout.copy(fontSize = 11.5.sp), color = palette.textFaint, modifier = Modifier.padding(bottom = 10.dp))
            if (agents.isEmpty()) {
                Panel { HbText("…", style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint) }
            } else {
                agents.forEach { ag ->
                    VoiceAgentRow(ag, options, a.tuned) { editing = ag.agentId }
                    Spacer(Modifier.height(8.dp))
                }
            }
        } else {
            VoiceEditor(
                agent = current,
                voiceOptions = options,
                onCancel = { editing = null },
                onSave = { patch ->
                    scope.launch {
                        val next = api.saveVoiceAgent(
                            config, current.agentId, patch.voiceId,
                            patch.stability, patch.similarityBoost, patch.style, patch.speed, patch.useSpeakerBoost,
                        )
                        if (next.isNotEmpty()) agents = next
                        editing = null
                    }
                },
                onClearAll = {
                    scope.launch {
                        val next = api.clearVoiceAgent(config, current.agentId)
                        if (next.isNotEmpty()) agents = next
                        editing = null
                    }
                },
            )
        }
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun VoiceAgentRow(agent: VoiceAgentInfo, options: List<VoiceOption>, tunedLabel: String, onEdit: () -> Unit) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val display = options.firstOrNull { it.id == agent.effectiveVoice }?.display ?: agent.effectiveVoice
    Panel {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Column(Modifier.weight(1f)) {
                HbText(agent.name, style = HbType.read.copy(fontSize = 14.sp), color = palette.text, maxLines = 1)
                HbText(
                    display + if (agent.voiceSettings != null) " · $tunedLabel" else "",
                    style = HbType.readout.copy(fontSize = 11.sp),
                    color = palette.textFaint,
                    maxLines = 1,
                )
            }
            SettingsButton(t.settingsAutomations.edit, onClick = onEdit)
        }
    }
}

/** The values a save carries — a flat bag rather than the wire shape, so the
 *  screen doesn't need to know PUT /voice/agents/{id}'s JSON layout. */
private data class VoicePatch(
    val voiceId: String?,
    val stability: Float,
    val similarityBoost: Float,
    val style: Float,
    val speed: Float,
    val useSpeakerBoost: Boolean,
)

private object VoiceDefaults {
    const val STABILITY = 0.5f
    const val SIMILARITY = 0.75f
    const val STYLE = 0f
    const val SPEED = 1.0f
    const val SPEAKER_BOOST = true
}

@Composable
private fun VoiceEditor(
    agent: VoiceAgentInfo,
    voiceOptions: List<VoiceOption>,
    onCancel: () -> Unit,
    onSave: (VoicePatch) -> Unit,
    onClearAll: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val a = t.settingsVoices

    var voiceId by remember(agent.agentId) { mutableStateOf(agent.voiceId ?: "") }
    // The bare ElevenLabs voice id for manual entry — a fallback for when the
    // catalog can't be listed (the key may lack voices_read) but synthesis
    // itself still works fine on text_to_speech alone.
    var manualId by remember(agent.agentId) {
        mutableStateOf(
            agent.voiceId?.takeIf { it.startsWith("elevenlabs:") }
                ?.split(':')?.drop(2)?.joinToString(":") ?: "",
        )
    }
    var stability by remember(agent.agentId) { mutableStateOf(agent.voiceSettings?.stability ?: VoiceDefaults.STABILITY) }
    var similarity by remember(agent.agentId) { mutableStateOf(agent.voiceSettings?.similarityBoost ?: VoiceDefaults.SIMILARITY) }
    var style by remember(agent.agentId) { mutableStateOf(agent.voiceSettings?.style ?: VoiceDefaults.STYLE) }
    var speed by remember(agent.agentId) { mutableStateOf(agent.voiceSettings?.speed ?: VoiceDefaults.SPEED) }
    var speakerBoost by remember(agent.agentId) { mutableStateOf(agent.voiceSettings?.useSpeakerBoost ?: VoiceDefaults.SPEAKER_BOOST) }
    var busy by remember { mutableStateOf(false) }

    // Grouped by engine so the owner's ElevenLabs voices sit apart from
    // Azure/OpenAI's, never interleaved — same idea as the agent picker
    // grouping in the automation builder.
    val byProvider = remember(voiceOptions) { voiceOptions.groupBy { it.provider } }

    fun pickFromCatalog(ref: String) {
        voiceId = ref
        manualId = if (ref.startsWith("elevenlabs:")) ref.split(':').drop(2).joinToString(":") else ""
    }
    fun typeManualId(raw: String) {
        manualId = raw
        voiceId = if (raw.isNotBlank()) "elevenlabs:eleven_multilingual_v2:${raw.trim()}" else ""
    }

    Column {
        HbText(agent.name, style = HbType.headerBar.copy(fontSize = 17.sp), color = palette.text, modifier = Modifier.padding(bottom = 14.dp))

        FieldLabel(a.voicePick)
        Hint(a.voicePickHint)
        Spacer(Modifier.height(8.dp))
        Panel {
            VoiceOptionRow(
                label = "${a.useDefault} — ${agent.defaultVoice}",
                selected = !voiceOptions.any { it.id == voiceId },
            ) { voiceId = "" ; manualId = "" }
            byProvider.forEach { (provider, opts) ->
                Spacer(Modifier.height(4.dp))
                HbText(provider, style = HbType.readout.copy(fontSize = 9.5.sp), color = palette.textFaint, modifier = Modifier.padding(top = 4.dp, bottom = 2.dp))
                opts.forEach { o ->
                    VoiceOptionRow(label = o.display, selected = voiceId == o.id) { pickFromCatalog(o.id) }
                }
            }
        }

        Spacer(Modifier.height(14.dp))
        FieldLabel(a.manualVoiceId)
        Hint(a.manualVoiceIdHint)
        Spacer(Modifier.height(8.dp))
        GlassField(manualId, ::typeManualId, placeholder = a.manualVoiceIdPlaceholder, singleLine = true, mono = true)

        SectionHeader(a.tuning)
        HbText(a.tuningHint, style = HbType.readout.copy(fontSize = 11.5.sp), color = palette.textFaint, modifier = Modifier.padding(bottom = 12.dp))

        SliderField(
            label = a.stability, value = stability, range = 0f..1f, steps = 99,
            low = a.variable, high = a.stable, onChange = { stability = it },
        )
        Spacer(Modifier.height(16.dp))
        SliderField(
            label = a.similarity, value = similarity, range = 0f..1f, steps = 99,
            low = a.low, high = a.high, onChange = { similarity = it },
        )
        Spacer(Modifier.height(16.dp))
        SliderField(
            label = a.style, value = style, range = 0f..1f, steps = 99,
            low = a.none, high = a.exaggerated, onChange = { style = it },
        )
        Spacer(Modifier.height(16.dp))
        SliderField(
            label = a.speed, value = speed, range = 0.7f..1.2f, steps = 49,
            low = a.slower, high = a.faster, onChange = { speed = it },
        )

        Spacer(Modifier.height(14.dp))
        SettingsRow(title = a.speakerBoost) {
            HbToggle(checked = speakerBoost, onToggle = { speakerBoost = it })
        }

        Spacer(Modifier.height(16.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            SettingsButton(
                if (busy) t.settingsAutomations.saving else t.settingsAutomations.save,
                enabled = !busy,
                onClick = {
                    busy = true
                    onSave(VoicePatch(voiceId.ifBlank { null }, stability, similarity, style, speed, speakerBoost))
                },
            )
            SettingsButton(t.settingsAutomations.cancel, onClick = onCancel, tint = palette.textDim)
            SettingsButton(a.resetAll, onClick = onClearAll, tint = palette.red)
        }
    }
}

@Composable
private fun VoiceOptionRow(label: String, selected: Boolean, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(4.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 8.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(
            Modifier
                .size(14.dp)
                .clip(RoundedCornerShape(50))
                .border(1.dp, if (selected) palette.accentBright else palette.textFaint, RoundedCornerShape(50)),
        ) {
            if (selected) {
                Box(Modifier.fillMaxSize().padding(3.dp).clip(RoundedCornerShape(50)).background(palette.accentBright))
            }
        }
        HbText(label, style = HbType.read.copy(fontSize = 13.sp), color = if (selected) palette.accentBright else palette.text, maxLines = 1)
    }
}
