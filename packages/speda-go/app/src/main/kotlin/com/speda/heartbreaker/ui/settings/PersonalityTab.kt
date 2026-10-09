// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later
package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.AgentPersonalityInfo
import com.speda.heartbreaker.data.PersonalitySettings
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

@Composable
fun PersonalityTab(config: AppConfig, graph: AppGraph) {
    val t = LocalStrings.current
    val a = t.settingsPersonality
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()
    var agents by remember { mutableStateOf<List<AgentPersonalityInfo>>(emptyList()) }
    var drafts by remember { mutableStateOf<Map<String, PersonalitySettings>>(emptyMap()) }
    var selected by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var saved by remember { mutableStateOf(false) }
    var reload by remember { mutableStateOf(0) }
    LaunchedEffect(config, reload) {
        loading = true
        error = ""
        try {
            agents = graph.api.fetchAgentPersonalities(config)
            drafts = agents.associate { it.agentId to it.settings }
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { error = a.loadFailed }
        finally { loading = false }
    }
    val current = agents.firstOrNull { it.agentId == selected }
    val draft = selected?.let(drafts::get)
    val dirty = current != null && draft != null && current.settings != draft
    fun change(next: PersonalitySettings) {
        current ?: return
        drafts = drafts + (current.agentId to next)
        saved = false
        error = ""
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(16.dp)) {
        Hint(a.blurb)
        Spacer(Modifier.height(14.dp))
        if (loading) Hint(a.loading)
        if (error.isNotEmpty()) {
            HbText(error, style = HbType.read, color = palette.red)
            if (agents.isEmpty()) SettingsButton(a.retry, { reload++ })
        }
        if (!loading && current == null) {
            agents.forEach { agent ->
                SettingsRow(agent.name, agent.domain) {
                    SettingsButton(t.common.edit, { selected = agent.agentId; saved = false; error = "" })
                }
                Spacer(Modifier.height(10.dp))
            }
        } else if (!loading && current != null && draft != null) {
            SettingsButton(t.common.close, { selected = null; error = "" }, enabled = !busy)
            SectionHeader(current.name)
            PersonalityChoices(a.tone, draft.tone, listOf("default" to a.profileDefault, "familiar" to a.familiar, "professional" to a.professional), !busy) { change(draft.copy(tone = it)) }
            PersonalityChoices(a.humor, draft.humor, listOf("default" to a.profileDefault, "off" to a.off, "dry" to a.dry, "playful" to a.playful), !busy) { change(draft.copy(humor = it)) }
            PersonalityChoices(a.directness, draft.directness, listOf("default" to a.profileDefault, "gentle" to a.gentle, "direct" to a.direct), !busy) { change(draft.copy(directness = it)) }
            PersonalityChoices(a.responseLength, draft.responseLength, listOf("default" to a.profileDefault, "brief" to a.brief, "detailed" to a.detailed), !busy) { change(draft.copy(responseLength = it)) }
            SectionHeader(a.instructions)
            GlassField(draft.instructions, { if (!busy) change(draft.copy(instructions = it.take(6000))) }, a.placeholder, singleLine = false, minHeight = 180.dp, dirty = dirty)
            Spacer(Modifier.height(10.dp))
            Hint("${a.instructionsHint} · ${draft.instructions.length}/6000")
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                SettingsButton(if (busy) t.settingsAutomations.saving else t.common.save, enabled = dirty && !busy, onClick = {
                    busy = true
                    error = ""
                    saved = false
                    scope.launch {
                        try {
                            val next = graph.api.saveAgentPersonality(config, current.agentId, draft)
                            val persisted = next.firstOrNull { it.agentId == current.agentId } ?: kotlin.error("Missing saved agent")
                            agents = next
                            drafts = drafts + (current.agentId to persisted.settings)
                            saved = true
                        } catch (e: CancellationException) { throw e }
                        catch (_: Exception) { error = a.saveFailed }
                        finally { busy = false }
                    }
                })
                SettingsButton(a.reset, { change(PersonalitySettings()) }, enabled = !busy)
            }
            Spacer(Modifier.height(8.dp))
            Hint(if (dirty) a.unsaved else if (saved) a.saved else a.current)
        }
    }
}

@Composable
private fun PersonalityChoices(label: String, value: String, options: List<Pair<String, String>>, enabled: Boolean, onChange: (String) -> Unit) {
    val palette = LocalHbPalette.current
    FieldLabel(label)
    Row(Modifier.horizontalScroll(rememberScrollState()).padding(bottom = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { (key, text) ->
            SettingsButton(text, { onChange(key) }, enabled = enabled, tint = if (key == value) palette.accent else null)
        }
    }
}
