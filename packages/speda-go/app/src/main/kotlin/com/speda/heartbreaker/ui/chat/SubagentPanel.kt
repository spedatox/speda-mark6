// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.chat

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.SubagentRun
import com.speda.heartbreaker.domain.SubagentStep
import com.speda.heartbreaker.ui.HbText
import com.speda.heartbreaker.ui.prose.ProseText

/**
 * What a coding peer delegated during a turn — mobile port of
 * SubagentPanel.tsx / SubagentDetailView.tsx.
 *
 * COLLAPSED BY DEFAULT, same reason as the desktop: the parent's reply already
 * summarises what the delegate did, so this is the receipt, opened only when
 * that summary is not enough. It is never part of the message bubble's own
 * prose — see [SubagentRun]'s doc for why streaming it as text was tried and
 * reverted.
 */
@Composable
fun SubagentPanel(runs: List<SubagentRun>, modifier: Modifier = Modifier) {
    if (runs.isEmpty()) return
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        runs.forEach { run -> SubagentRunCard(run) }
    }
}

@Composable
private fun SubagentRunCard(run: SubagentRun) {
    val palette = LocalHbPalette.current
    var open by remember { mutableStateOf(false) }

    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(palette.text.copy(alpha = 0.025f))
            .animateContentSize(),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .clickable { open = !open }
                .padding(horizontal = 12.dp, vertical = 9.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            val dotColor = when {
                run.running -> palette.accentBright
                run.ok == false -> palette.red
                else -> palette.green
            }
            Box(Modifier.size(6.dp).background(dotColor, CircleShape))
            Column(Modifier.weight(1f)) {
                HbText(
                    run.label.ifEmpty { run.agent.ifEmpty { "Subagent" } },
                    style = HbType.read.copy(fontSize = 12.5.sp),
                    color = palette.text,
                    maxLines = 1,
                )
                if (run.agent.isNotEmpty() && run.label.isNotEmpty()) {
                    HbText(run.agent, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint, maxLines = 1)
                }
            }
            HbText(
                if (run.running) "running" else if (run.ok == false) "failed" else "done",
                style = HbType.readout.copy(fontSize = 10.sp),
                color = dotColor,
            )
        }

        if (open) {
            Column(Modifier.padding(start = 12.dp, end = 12.dp, bottom = 12.dp)) {
                run.prompt?.takeIf { it.isNotBlank() }?.let {
                    HbText(it, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textDim)
                    Spacer(Modifier.height(8.dp))
                }
                run.steps.forEach { step -> SubagentStepView(step) }
                run.report?.takeIf { it.isNotBlank() }?.let {
                    Spacer(Modifier.height(8.dp))
                    ProseText(it)
                }
            }
        }
    }
}

@Composable
private fun SubagentStepView(step: SubagentStep) {
    val palette = LocalHbPalette.current
    when (step.kind) {
        "text" -> step.text?.takeIf { it.isNotBlank() }?.let {
            ProseText(it, modifier = Modifier.padding(vertical = 4.dp))
        }
        "tool" -> Row(
            Modifier.fillMaxWidth().padding(vertical = 3.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            HbText("›", style = HbType.readout.copy(fontSize = 11.sp), color = palette.accentDim)
            HbText(
                step.tool ?: "tool",
                style = HbType.readout.copy(fontSize = 11.sp),
                color = palette.textDim,
                maxLines = 1,
            )
        }
    }
}
