package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.ReminderCycleInfo
import com.speda.heartbreaker.data.ReminderDefinition
import com.speda.heartbreaker.data.ReminderOption
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.launch

private val AGENTS = listOf("speda", "atomix", "ultron", "sentinel", "nightcrawler", "centurion", "orion")

/** "1,3,5" ⇄ chips. "*" is every day. */
private fun daysToSet(days: String): Set<Int> =
    if (days.isBlank() || days == "*") setOf(1, 2, 3, 4, 5, 6, 7)
    else days.split(",").mapNotNull { it.trim().toIntOrNull() }.toSet()

private fun setToDays(set: Set<Int>): String =
    if (set.size == 7) "*" else set.sorted().joinToString(",")

/**
 * Settings ▸ Reminders — standing questions Igor asks over the owning agent's
 * Telegram bot and re-asks until answered.
 *
 * The text is sent verbatim, so it is written here by hand and no model touches
 * it — which is also why asking costs nothing. Reminders an agent composes
 * itself (Atomix's evening checklist) are not defined here; they appear only in
 * the history at the bottom.
 */
@Composable
fun RemindersTab(config: AppConfig, graph: AppGraph) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val scope = rememberCoroutineScope()
    val api = graph.api

    var defs by remember { mutableStateOf<List<ReminderDefinition>>(emptyList()) }
    var history by remember { mutableStateOf<List<ReminderCycleInfo>>(emptyList()) }
    var draft by remember { mutableStateOf<ReminderDefinition?>(null) }
    var error by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }

    suspend fun reload() {
        defs = api.getReminders(config)
        history = api.getReminderHistory(config, 20)
    }
    LaunchedEffect(config) { reload() }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        SectionHeader(t.settingsReminders.title)
        Hint(t.settingsReminders.intro)
        Spacer(Modifier.height(8.dp))

        Panel {
            if (defs.isEmpty()) {
                HbText(
                    t.settingsReminders.noneConfigured,
                    style = HbType.readout.copy(fontSize = 12.sp),
                    color = palette.textFaint,
                )
            }
            defs.forEach { d ->
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    HbText(
                        d.at.ifBlank { "—" },
                        style = HbType.readout.copy(fontSize = 13.sp, fontWeight = FontWeight.Bold),
                        color = if (d.enabled) palette.accentBright else palette.textFaint,
                        modifier = Modifier.width(48.dp),
                    )
                    Column(Modifier.weight(1f).clickable { draft = d.copy(); error = "" }) {
                        HbText(
                            d.text.take(70),
                            style = HbType.read.copy(fontSize = 14.sp),
                            color = if (d.enabled) palette.text else palette.textFaint,
                        )
                        val days = if (d.days == "*") t.settingsReminders.everyDay
                        else d.days.split(",").mapNotNull { it.trim().toIntOrNull() }
                            .joinToString(" ") { t.settingsReminders.dayLabels[it - 1] }
                        HbText(
                            "${d.id} · ${d.agent} · $days · every ${d.everyMinutes}m, max ${d.maxAsks}",
                            style = HbType.readout.copy(fontSize = 11.sp),
                            color = palette.textFaint,
                        )
                    }
                    HbToggle(
                        checked = d.enabled,
                        enabled = !busy,
                        onToggle = { on ->
                            scope.launch {
                                api.saveReminder(config, d.copy(enabled = on)); reload()
                            }
                        },
                    )
                }
            }
        }

        Spacer(Modifier.height(12.dp))

        val editing = draft
        if (editing == null) {
            SettingsButton(t.settingsReminders.newReminder, onClick = {
                draft = ReminderDefinition(
                    id = "", agent = "atomix", text = "", at = "09:00", days = "*",
                    options = listOf(ReminderOption(t.settingsReminders.defaultDoneLabel, "done")),
                    everyMinutes = 5, maxAsks = 10, enabled = true,
                )
                error = ""
            })
        } else {
            val isNew = defs.none { it.id == editing.id }
            SectionHeader(if (isNew) t.settingsReminders.newReminderTitle else t.settingsReminders.editReminder(editing.id))
            Panel {
                if (isNew) {
                    FieldLabel(t.settingsReminders.idLabel)
                    GlassField(
                        value = editing.id,
                        onValueChange = { v ->
                            draft = editing.copy(id = v.lowercase().replace(Regex("[^a-z0-9_]"), "_"))
                        },
                        placeholder = "lustral_evening",
                        singleLine = true,
                    )
                    Spacer(Modifier.height(10.dp))
                }

                FieldLabel(t.settingsReminders.messageLabel)
                GlassField(
                    value = editing.text,
                    onValueChange = { draft = editing.copy(text = it) },
                    placeholder = "💊 150 mg Lustral aldın mı?",
                    singleLine = false,
                    minHeight = 76.dp,
                )
                Spacer(Modifier.height(10.dp))

                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Column(Modifier.weight(1f)) {
                        FieldLabel(t.settingsReminders.timeLabel)
                        GlassField(
                            value = editing.at,
                            onValueChange = { draft = editing.copy(at = it.take(5)) },
                            placeholder = "23:00", singleLine = true, mono = true,
                        )
                    }
                    Column(Modifier.weight(1f)) {
                        FieldLabel(t.settingsReminders.reaskLabel)
                        GlassField(
                            value = editing.everyMinutes.toString(),
                            onValueChange = { v ->
                                draft = editing.copy(everyMinutes = v.filter { it.isDigit() }.toIntOrNull() ?: 5)
                            },
                            placeholder = "5", singleLine = true, mono = true,
                        )
                    }
                    Column(Modifier.weight(1f)) {
                        FieldLabel(t.settingsReminders.giveUpLabel)
                        GlassField(
                            value = editing.maxAsks.toString(),
                            onValueChange = { v ->
                                draft = editing.copy(maxAsks = v.filter { it.isDigit() }.toIntOrNull() ?: 10)
                            },
                            placeholder = "10", singleLine = true, mono = true,
                        )
                    }
                }
                Spacer(Modifier.height(10.dp))

                FieldLabel(t.settingsReminders.daysLabel)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    t.settingsReminders.dayLabels.forEachIndexed { i, dl ->
                        val n = i + 1
                        val set = daysToSet(editing.days)
                        TabChip(dl, active = set.contains(n)) {
                            val next = set.toMutableSet()
                            if (!next.remove(n)) next.add(n)
                            if (next.isNotEmpty()) draft = editing.copy(days = setToDays(next))
                        }
                    }
                }
                Spacer(Modifier.height(10.dp))

                FieldLabel(t.settingsReminders.askedByLabel)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    AGENTS.take(4).forEach { a ->
                        TabChip(a, active = editing.agent == a) { draft = editing.copy(agent = a) }
                    }
                }
                Spacer(Modifier.height(10.dp))

                FieldLabel(t.settingsReminders.answerButtonsLabel)
                editing.options.forEachIndexed { i, opt ->
                    Row(
                        Modifier.fillMaxWidth().padding(vertical = 3.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Column(Modifier.weight(1f)) {
                            GlassField(
                                value = opt.label,
                                onValueChange = { v ->
                                    val options = editing.options.toMutableList()
                                    // The value is what lands in the history — derive it once
                                    // and keep it, so rewording a label never forks the record.
                                    options[i] = opt.copy(
                                        label = v,
                                        value = opt.value.ifBlank {
                                            v.lowercase().replace(" ", "_").take(24)
                                        },
                                    )
                                    draft = editing.copy(options = options)
                                },
                                placeholder = "✅ Aldım", singleLine = true,
                            )
                        }
                        if (editing.options.size > 1) {
                            TabChip("×", active = false) {
                                draft = editing.copy(
                                    options = editing.options.filterIndexed { j, _ -> j != i },
                                )
                            }
                        }
                    }
                }
                if (editing.options.size < 6) {
                    Spacer(Modifier.height(4.dp))
                    TabChip(t.settingsReminders.addButton, active = false) {
                        draft = editing.copy(options = editing.options + ReminderOption("", ""))
                    }
                }

                if (error.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    HbText(error, style = HbType.readout.copy(fontSize = 12.sp), color = palette.amber)
                }

                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SettingsButton(if (busy) t.settingsReminders.saving else t.settingsReminders.save, enabled = !busy, onClick = {
                        if (editing.id.isBlank() || editing.text.isBlank()) {
                            error = t.settingsReminders.idRequired
                        } else {
                            busy = true; error = ""
                            scope.launch {
                                val ok = api.saveReminder(config, editing)
                                busy = false
                                if (ok) { draft = null; reload() } else error = t.settingsReminders.saveFailed
                            }
                        }
                    })
                    SettingsButton(t.settingsReminders.cancel, onClick = { draft = null; error = "" })
                    if (!isNew) {
                        SettingsButton(t.settingsReminders.delete, tint = palette.amber, onClick = {
                            scope.launch {
                                api.deleteReminder(config, editing.id)
                                draft = null
                                reload()
                            }
                        })
                    }
                }
            }
        }

        if (history.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
            SectionHeader(t.settingsReminders.recent)
            Panel {
                history.forEach { h ->
                    Row(
                        Modifier.fillMaxWidth().padding(vertical = 3.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        HbText(h.day, style = HbType.readout.copy(fontSize = 11.sp),
                            color = palette.textFaint, modifier = Modifier.width(74.dp))
                        HbText(h.reminderId, style = HbType.readout.copy(fontSize = 11.sp),
                            color = palette.textDim, modifier = Modifier.weight(1f))
                        HbText(
                            if (h.status == "answered") h.answer else t.settingsReminders.noAnswer,
                            style = HbType.readout.copy(fontSize = 11.sp),
                            color = if (h.status == "answered") palette.accentBright else palette.amber,
                        )
                        HbText("${h.asks}×", style = HbType.readout.copy(fontSize = 11.sp),
                            color = palette.textFaint)
                    }
                }
            }
        }

        Spacer(Modifier.height(24.dp))
    }
}
