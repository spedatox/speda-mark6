// SPDX-License-Identifier: AGPL-3.0-or-later
package com.speda.heartbreaker.ui.prose

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.ArtifactSpec
import com.speda.heartbreaker.domain.artifactAnswerMatches
import com.speda.heartbreaker.domain.looksIncomplete
import com.speda.heartbreaker.domain.parseNativeArtifact
import com.speda.heartbreaker.i18n.LocalStrings
import kotlin.math.roundToInt

@Composable
fun NativeArtifact(raw: String, title: String) {
    val labels = LocalStrings.current.proseKind
    val palette = LocalHbPalette.current
    val spec = remember(raw) { parseNativeArtifact(raw) }
    if (spec == null) {
        if (looksIncomplete(raw)) Materializing(labels.widget) else ParseError(labels.widget, raw)
        return
    }
    val name = if (spec is ArtifactSpec.Practice) labels.practiceLab else labels.simulation
    ChartPanel(title.ifBlank { name }) {
        Column(Modifier.padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
            Text("◇  " + name.uppercase(), color = palette.accentBright, style = HbType.label)
            Text(spec.description, color = palette.textDim, style = HbType.read)
            HorizontalDivider(color = palette.edge)
            key(raw) {
                when (spec) {
                    is ArtifactSpec.Practice -> PracticeLab(spec)
                    is ArtifactSpec.Explore -> ExploreLab(spec)
                }
            }
        }
    }
}

private enum class Result { CORRECT, RETRY, REVEALED }
private data class Attempt(val text: String = "", val selected: Int? = null, val result: Result? = null)

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PracticeLab(spec: ArtifactSpec.Practice) {
    val t = LocalStrings.current.proseKind
    val palette = LocalHbPalette.current
    var index by remember { mutableIntStateOf(0) }
    val attempts = remember { mutableStateMapOf<Int, Attempt>() }
    val item = spec.items[index]
    val attempt = attempts[index] ?: Attempt()
    val solved = attempts.values.count { it.result == Result.CORRECT }
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        spec.items.forEachIndexed { i, _ ->
            val complete = attempts[i]?.result == Result.CORRECT
            val description = "${t.question} ${i + 1}" + if (complete) " · ${t.correctAnswer}" else ""
            OutlinedButton(
                onClick = { index = i },
                modifier = Modifier.semantics { contentDescription = description },
                colors = ButtonDefaults.outlinedButtonColors(
                    contentColor = if (i == index) palette.accentBright else palette.textDim,
                    containerColor = if (i == index) palette.accent.copy(alpha = .14f) else palette.base,
                ),
            ) { Text((i + 1).toString().padStart(2, '0') + if (complete) " ✓" else "") }
        }
    }
    Text("$solved/${spec.items.size} ${t.solved}", style = HbType.label, color = palette.textDim)
    Text(item.prompt, style = HbType.read, color = palette.text, fontWeight = FontWeight.SemiBold)
    item.code?.let { code ->
        Text(code, modifier = Modifier.fillMaxWidth().background(palette.base, RoundedCornerShape(8.dp))
            .border(1.dp, palette.edge, RoundedCornerShape(8.dp)).horizontalScroll(rememberScrollState()).padding(10.dp),
            color = palette.text, fontFamily = FontFamily.Monospace, fontSize = 13.sp, softWrap = false)
    }
    val choice = item.choice
    if (choice == null) {
        OutlinedTextField(
            value = attempt.text, onValueChange = { if (it.length <= 240) attempts[index] = Attempt(text = it) },
            modifier = Modifier.fillMaxWidth(), label = { Text(t.prediction) }, maxLines = 4,
        )
    } else {
        Text(t.chooseAnswer, style = HbType.label, color = palette.textDim)
        Column(Modifier.selectableGroup(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            choice.options.forEachIndexed { i, option ->
                val selected = attempt.selected == i
                val shape = RoundedCornerShape(8.dp)
                Row(
                    Modifier.fillMaxWidth().border(1.dp, if (selected) palette.accent else palette.edge, shape)
                        .background(if (selected) palette.accent.copy(alpha = .12f) else palette.base, shape)
                        .selectable(selected = selected, onClick = { attempts[index] = Attempt(selected = i) }, role = Role.RadioButton)
                        .padding(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    RadioButton(selected, onClick = null, colors = RadioButtonDefaults.colors(selectedColor = palette.accentBright))
                    Text("${'A' + i}", color = palette.accentBright, fontFamily = FontFamily.Monospace)
                    Text(option, Modifier.weight(1f), color = palette.text, style = HbType.read)
                }
            }
        }
    }
    Button(
        onClick = {
            val correct = if (choice != null) attempt.selected == choice.answerIndex else artifactAnswerMatches(attempt.text, item.answer)
            val result = if (correct) Result.CORRECT else Result.RETRY
            attempts[index] = attempt.copy(result = result)
        },
        enabled = if (choice != null) attempt.selected != null else attempt.text.isNotBlank(),
        colors = ButtonDefaults.buttonColors(containerColor = palette.accent.copy(alpha = .20f), contentColor = palette.accentBright),
    ) { Text(t.checkAnswer) }
    when (attempt.result) {
        Result.RETRY -> Row(verticalAlignment = Alignment.CenterVertically) {
            Text(t.tryAgain, Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite }, color = palette.textDim)
            TextButton(onClick = { attempts[index] = attempt.copy(result = Result.REVEALED) }) { Text(t.revealSolution) }
        }
        Result.CORRECT, Result.REVEALED -> {
            val correct = attempt.result == Result.CORRECT
            val color = if (correct) palette.green else palette.accentBright
            Column(Modifier.fillMaxWidth().background(color.copy(alpha = .08f), RoundedCornerShape(8.dp))
                .padding(10.dp).semantics { liveRegion = LiveRegionMode.Polite }, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(if (correct) t.correctAnswer else t.solution, color = color, fontWeight = FontWeight.SemiBold)
                Text(item.answer, color = palette.text, fontFamily = if (choice == null) FontFamily.Monospace else null)
                Text(item.explanation, color = palette.textDim, style = HbType.read)
            }
        }
        null -> Unit
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        TextButton(onClick = { attempts.clear(); index = 0 }, enabled = attempts.isNotEmpty()) { Text(t.resetArtifact) }
        if (index < spec.items.lastIndex) TextButton(onClick = { index++ }) { Text(t.nextQuestion + " →") }
    }
}

@Composable
private fun ExploreLab(spec: ArtifactSpec.Explore) {
    val t = LocalStrings.current.proseKind
    val palette = LocalHbPalette.current
    val parameter = spec.parameter
    var value by remember { mutableIntStateOf(parameter.initial) }
    val active = spec.activeCase(value)
    val outcome = spec.cases[active]
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(parameter.label, color = palette.text, fontWeight = FontWeight.SemiBold)
        Text("$value" + (parameter.unit?.let { " $it" } ?: ""), color = palette.accentBright, style = HbType.readout)
    }
    Slider(value = value.toFloat(), onValueChange = { value = it.roundToInt() },
        valueRange = parameter.min.toFloat()..parameter.max.toFloat(),
        modifier = Modifier.semantics { contentDescription = parameter.label },
        colors = SliderDefaults.colors(thumbColor = palette.accentBright, activeTrackColor = palette.accent, inactiveTrackColor = palette.edge))
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(parameter.min.toString(), color = palette.textDim, style = HbType.label)
        Text(parameter.max.toString(), color = palette.textDim, style = HbType.label)
    }
    Column(Modifier.fillMaxWidth().background(palette.accent.copy(alpha = .09f), RoundedCornerShape(8.dp))
        .padding(10.dp).semantics { liveRegion = LiveRegionMode.Polite }, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(t.liveOutcome.uppercase(), style = HbType.label, color = palette.textDim)
        Text(outcome.label, color = palette.accentBright, fontWeight = FontWeight.SemiBold, fontSize = 18.sp)
        Text(outcome.detail, color = palette.text, style = HbType.read)
    }
    spec.cases.forEachIndexed { index, step ->
        val min = if (index == 0) parameter.min else spec.cases[index - 1].through + 1
        val range = if (min == step.through) "$min" else "$min – ${step.through}"
        val color = if (index == active) palette.accentBright else palette.textDim
        Row(Modifier.fillMaxWidth().border(1.dp, if (index == active) palette.accent else palette.edge, RoundedCornerShape(7.dp))
            .padding(horizontal = 9.dp, vertical = 5.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(range, color = color, fontFamily = FontFamily.Monospace, fontSize = 12.sp)
            Text(step.label, color = color, style = HbType.read)
        }
    }
    TextButton(onClick = { value = parameter.initial }, enabled = value != parameter.initial) { Text(t.resetArtifact) }
}
