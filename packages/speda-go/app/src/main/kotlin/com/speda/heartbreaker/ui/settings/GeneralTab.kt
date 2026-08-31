// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.HbSettings
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun GeneralTab(config: AppConfig, graph: AppGraph, settings: HbSettings) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val scope = rememberCoroutineScope()

    var prompt by remember { mutableStateOf(settings.systemPrompt) }
    LaunchedEffect(prompt) { delay(400); graph.settings.setSystemPrompt(prompt) }

    var temp by remember { mutableStateOf(settings.temperature) }

    var budget by remember { mutableStateOf<Boolean?>(null) }
    LaunchedEffect(config) { budget = graph.api.getBudgetMode(config) }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        SectionHeader(t.settingsGeneral.systemPrompt)
        Panel {
            Hint(t.settingsGeneral.systemPromptHint)
            Spacer(Modifier.height(8.dp))
            GlassField(prompt, { prompt = it }, t.settingsGeneral.systemPromptPlaceholder, singleLine = false, minHeight = 120.dp)
        }

        SectionHeader(t.settingsGeneral.temperature)
        Panel {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                HbText(t.settingsGeneral.sampling, style = HbType.read.copy(fontSize = 14.sp), color = palette.text, modifier = Modifier.weight(1f))
                HbText(String.format("%.1f", temp), style = HbType.readout.copy(fontSize = 13.sp), color = palette.accentBright)
            }
            Hint(t.settingsGeneral.temperatureHint)
            Spacer(Modifier.height(6.dp))
            Slider(
                value = temp,
                onValueChange = { temp = it },
                onValueChangeFinished = { scope.launch { graph.settings.setTemperature(temp) } },
                valueRange = 0f..1f,
                steps = 9,
                colors = SliderDefaults.colors(
                    thumbColor = palette.accent,
                    activeTrackColor = palette.accent,
                    inactiveTrackColor = palette.accent.copy(alpha = 0.25f),
                ),
            )
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                HbText(t.settingsGeneral.precise, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
                HbText(t.settingsGeneral.creative, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
            }
        }

        SectionHeader(t.settingsGeneral.behaviour)
        Panel {
            ToggleRow(
                label = t.settingsGeneral.budgetMode,
                subtitle = t.settingsGeneral.budgetModeHint,
                checked = budget == true,
                enabled = budget != null,
                onToggle = { next ->
                    budget = next
                    scope.launch { budget = graph.api.setBudgetMode(config, next) }
                },
            )
        }

        Spacer(Modifier.height(24.dp))
    }
}
