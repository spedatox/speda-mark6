package com.speda.heartbreaker.ui.systems

import android.app.DatePickerDialog
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import com.speda.heartbreaker.data.IaconStateFields
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.MemoryFileInfo
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbText
import com.speda.heartbreaker.ui.settings.GlassField
import com.speda.heartbreaker.ui.settings.Panel
import com.speda.heartbreaker.ui.settings.SettingsButton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import java.time.LocalDate
import java.util.UUID

private val statuses = listOf("active", "waiting", "planned", "completed", "cancelled", "superseded")
private val priorities = listOf("high", "normal", "low")

@Composable
internal fun StateEditorDialog(
    config: AppConfig,
    api: IgorApi,
    file: MemoryFileInfo?,
    onClose: () -> Unit,
    onSaved: (MemoryFileInfo) -> Unit,
    onHistory: (() -> Unit)?,
) {
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()
    val today = LocalDate.now()
    val initial = remember(file) {
        if (file == null) IaconStateFields(verifiedOn = today.toString(), reviewOn = today.plusDays(7).toString())
        else runCatching { Json.decodeFromString<IaconStateFields>(file.content) }.getOrNull()
    }
    var title by remember(file) { mutableStateOf(file?.displayName.orEmpty()) }
    var draft by remember(file) { mutableStateOf(initial) }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var previous by remember { mutableStateOf<Pair<Pair<String, IaconStateFields>, String>?>(null) }

    Dialog(onDismissRequest = { if (!saving) onClose() }) {
        Panel {
            Column(Modifier.fillMaxHeight(0.87f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(4.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)) {
                HbText(if (file == null) "New state" else "Edit state", style = HbType.headerBar.copy(fontSize = 20.sp),
                    color = palette.text)
                val state = draft
                if (state == null) {
                    HbText("Could not read this state.", style = HbType.read, color = palette.red)
                    onHistory?.let { SettingsButton("File & history", it) }
                    SettingsButton("Close", onClose)
                    return@Column
                }
                val terminal = state.status in statuses.drop(3)
                StateText("Title", title, !saving, maxLength = 256) { title = it }
                StateText("Summary", state.summary, !saving, maxLength = 4000, multiline = true) {
                    draft = state.copy(summary = it)
                }
                StateChoice("Status", state.status, statuses, !saving) { draft = state.copy(status = it) }
                StateChoice("Priority", state.salience, priorities, !saving) { draft = state.copy(salience = it) }
                StateDate("Last confirmed", state.verifiedOn, !saving) { draft = state.copy(verifiedOn = it.orEmpty()) }
                StateDate("Review date", state.reviewOn, !saving) { draft = state.copy(reviewOn = it.orEmpty()) }
                StateDate("Start date", state.startsOn, !saving, optional = true) { draft = state.copy(startsOn = it) }
                StateDate("Expected end", state.endsOn, !saving, optional = true) { draft = state.copy(endsOn = it) }
                if (terminal) StateDate("Closed on", state.closedOn ?: today.toString(), !saving) {
                    draft = state.copy(closedOn = it)
                }
                StateText("Orion’s follow-up question", state.question, !saving, maxLength = 500, multiline = true) {
                    draft = state.copy(question = it)
                }
                if (error.isNotEmpty()) HbText(error, style = HbType.read, color = palette.red)
                onHistory?.let { SettingsButton("File & history", it, enabled = !saving) }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SettingsButton("Cancel", onClose, enabled = !saving)
                    SettingsButton(if (saving) "Saving…" else "Save state", enabled = !saving, tint = palette.accent,
                        onClick = {
                            val body = title.trim() to state.copy(summary = state.summary.trim(), question = state.question.trim(),
                                closedOn = if (terminal) state.closedOn ?: today.toString() else null)
                            val fields = body.second
                            error = when {
                                body.first.isBlank() || fields.summary.isBlank() || fields.question.isBlank() ->
                                    "Enter a title, summary, and follow-up question."
                                fields.reviewOn < fields.verifiedOn -> "Review date must be on or after the confirmation date."
                                fields.startsOn != null && fields.endsOn != null && fields.endsOn < fields.startsOn ->
                                    "Expected end must be on or after the start date."
                                else -> ""
                            }
                            if (error.isEmpty() && !saving) {
                                val requestId = previous?.takeIf { it.first == body }?.second ?: UUID.randomUUID().toString()
                                previous = body to requestId
                                saving = true
                                scope.launch {
                                    try {
                                        onSaved(api.saveMemoryState(config, body.first, fields, requestId, file))
                                    } catch (cancelled: CancellationException) {
                                        throw cancelled
                                    } catch (failure: Exception) {
                                        error = failure.message ?: "Could not save the state."
                                    } finally {
                                        saving = false
                                    }
                                }
                            }
                        })
                }
            }
        }
    }
}

@Composable
private fun StateText(label: String, value: String, enabled: Boolean, maxLength: Int,
    multiline: Boolean = false, onChange: (String) -> Unit) {
    StateLabel(label)
    GlassField(value, { if (it.length <= maxLength) onChange(it) }, label,
        singleLine = !multiline, enabled = enabled)
}

@Composable
private fun StateChoice(label: String, value: String, options: List<String>, enabled: Boolean, onChange: (String) -> Unit) {
    StateLabel(label)
    var expanded by remember { mutableStateOf(false) }
    Box {
        SettingsButton(value.replaceFirstChar { it.uppercase() }, { expanded = true }, enabled = enabled)
        DropdownMenu(expanded, onDismissRequest = { expanded = false }) {
            options.forEach { option ->
                DropdownMenuItem(text = { HbText(option.replaceFirstChar { it.uppercase() }, style = HbType.read) },
                    onClick = { onChange(option); expanded = false })
            }
        }
    }
}

@Composable
private fun StateDate(label: String, value: String?, enabled: Boolean, optional: Boolean = false, onChange: (String?) -> Unit) {
    val context = LocalContext.current
    StateLabel(label)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        SettingsButton(value ?: "Set date", enabled = enabled, onClick = {
            val date = value?.let { LocalDate.parse(it) } ?: LocalDate.now()
            DatePickerDialog(context, { _, year, month, day ->
                onChange(LocalDate.of(year, month + 1, day).toString())
            }, date.year, date.monthValue - 1, date.dayOfMonth).show()
        })
        if (optional && value != null) SettingsButton("Clear", { onChange(null) }, enabled = enabled)
    }
}

@Composable
private fun StateLabel(label: String) {
    HbText(label, style = HbType.read.copy(fontSize = 13.sp), color = LocalHbPalette.current.textDim)
}
