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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.key
import androidx.compose.runtime.rememberCoroutineScope
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
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.foldSubagentEvent
import com.speda.heartbreaker.domain.workerLabel
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.ui.settings.GlassField
import com.speda.heartbreaker.ui.settings.SettingsButton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import java.util.UUID
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
fun SubagentPanel(runs: List<SubagentRun>, modifier: Modifier = Modifier, config: AppConfig? = null, api: IgorApi? = null) {
    if (runs.isEmpty()) return
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        runs.forEach { run -> key(run.id) { SubagentRunCard(run, config, api) } }
    }
}

@Composable
private fun SubagentRunCard(initialRun: SubagentRun, config: AppConfig?, api: IgorApi?) {
    val palette = LocalHbPalette.current
    var open by remember { mutableStateOf(false) }
    var run by remember(initialRun.id) { mutableStateOf(initialRun) }
    var reconnect by remember { mutableStateOf(0) }
    var notice by remember { mutableStateOf("") }
    var draft by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var pendingInput by remember { mutableStateOf<Pair<String, String>?>(null) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(initialRun) {
        if ((initialRun.lastSequence ?: 0) >= (run.lastSequence ?: 0)) {
            run = initialRun.copy(model = initialRun.model ?: run.model,
                canInterrupt = initialRun.canInterrupt ?: run.canInterrupt, canMessage = initialRun.canMessage ?: run.canMessage)
        }
    }
    val executionId = run.executionId
    LaunchedEffect(open, executionId, config, api, reconnect) {
        if (!open || executionId == null || config == null || api == null) return@LaunchedEffect
        try {
            val metadata = api.inspectWorkerExecution(config, executionId)
            val snapshot = metadata.toRun()
            if ((metadata.lastSequence ?: 0) >= (run.lastSequence ?: 0)) run = run.copy(ownerAgent = snapshot.ownerAgent ?: run.ownerAgent, workerRole = snapshot.workerRole ?: run.workerRole,
                executionType = snapshot.executionType, model = snapshot.model, status = snapshot.status,
                running = snapshot.running, report = snapshot.report ?: run.report,
                canInterrupt = snapshot.canInterrupt, canMessage = snapshot.canMessage)
            api.attachWorkerExecution(config, executionId, run.lastSequence ?: 0).collect { envelope ->
                val data = envelope.data as? JsonObject
                if (envelope.type == "subagent" && data != null) run = foldSubagentEvent(run, data)
            }
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) { notice = "Connection lost. Reconnect to recover this task's progress." }
    }

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
                    HbText(workerLabel(run), style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint, maxLines = 2)
                }
            }
            HbText(
                run.status ?: if (run.running) "running" else if (run.ok == false) "failed" else "done",
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
                if (executionId != null && config != null && api != null) {
                    run.model?.let { HbText(it, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textDim) }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        SettingsButton("Stop task", enabled = !busy && run.running && run.canInterrupt != false, onClick = {
                            scope.launch {
                                busy = true
                                try {
                                    api.interruptWorker(config, executionId)
                                    notice = "Stop requested for this task. Waiting for its confirmed state."
                                } catch (cancelled: CancellationException) { throw cancelled }
                                catch (error: Exception) { notice = "Task stop was not confirmed." }
                                finally { busy = false }
                            }
                        })
                        SettingsButton("Reconnect", enabled = !busy, onClick = { notice = ""; reconnect += 1 })
                    }
                    if (run.running && run.canMessage != false) {
                        GlassField(draft, { draft = it.take(6000) }, "Steer this task at its next safe boundary", singleLine = false)
                        SettingsButton("Send to task", enabled = !busy && draft.isNotBlank(), onClick = {
                            val text = draft.trim()
                            if (pendingInput?.first != text) pendingInput = text to UUID.randomUUID().toString().replace("-", "")
                            val messageId = pendingInput!!.second
                            scope.launch {
                                busy = true
                                try {
                                    api.messageWorker(config, executionId, text, messageId)
                                    notice = "Input accepted. Delivery is recorded at a worker boundary."
                                    draft = ""
                                    pendingInput = null
                                } catch (cancelled: CancellationException) { throw cancelled }
                                catch (error: Exception) { notice = "Task input was not confirmed. Retrying unchanged text keeps the same input ID." }
                                finally { busy = false }
                            }
                        })
                    }
                    if (notice.isNotBlank()) HbText(notice, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textDim)
                    Spacer(Modifier.height(8.dp))
                }
                run.steps.forEach { step -> SubagentStepView(step) }
                if (run.activityPartial) HbText("Showing a bounded activity preview. The saved task journal retains earlier activity.",
                    style = HbType.readout.copy(fontSize = 10.sp), color = palette.textDim)
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
        "tool" -> Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
            HbText("› ${step.tool ?: "tool"}", style = HbType.readout.copy(fontSize = 11.sp), color = palette.accentDim)
            step.input?.let { HbText(it.toString().take(16000), style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint) }
            step.result?.let { HbText(it.take(16000), style = HbType.readout.copy(fontSize = 10.sp), color = palette.textDim) }
        }
    }
}
