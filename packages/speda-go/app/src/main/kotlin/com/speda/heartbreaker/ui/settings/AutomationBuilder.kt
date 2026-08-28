package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.window.Dialog
import com.speda.heartbreaker.data.AutomationAgent
import com.speda.heartbreaker.data.AutomationDayFlag
import com.speda.heartbreaker.data.AutomationDraft
import com.speda.heartbreaker.data.AutomationDraftSchedule
import com.speda.heartbreaker.data.AutomationInfo
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.i18n.AppStrings
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.launch

/**
 * The automation builder — mobile port of AutomationBuilder.tsx. Three
 * guarantees it exists to keep, same as the desktop:
 *
 *  1. NO CRON, EVER. The owner picks a frequency, a time and days; the
 *     backend compiles the cron and this side never shows one.
 *  2. THE FORM CANNOT BUILD A BROKEN AUTOMATION. Every shape the backend
 *     refuses is unreachable here.
 *  3. HIS WORDS ARE NEVER LOST. [instruction] holds what he typed; the
 *     polished rendering (when present) is shown read-only, never edited in
 *     its place.
 *
 * Rendered INLINE inside AutomationsTab rather than as a nested modal, same
 * reason as the desktop: a second overlay above the settings sheet fights it
 * for the back gesture.
 *
 * `at` and `date` use Material3's TimePicker/DatePicker (see [TimeField] and
 * [DateField] below) rather than the desktop's native `<input type=time/date>`
 * — the mobile-native equivalent, not a scope trim. Both operate in 24-hour /
 * UTC-midnight terms and hand back the exact 'HH:MM' / 'YYYY-MM-DD' strings
 * the backend expects, so nothing downstream of [submit] needed to change.
 */

private val SCHEDULE_TEMPLATES = listOf("briefing", "reminder", "proactive_ask")
private val HOOK_TEMPLATES = listOf("hook_keyword", "hook_address", "hook_mail")
private val WEEK = listOf(1, 2, 3, 4, 5, 6, 7)

private fun isHook(template: String?): Boolean = template != null && template in HOOK_TEMPLATES

private fun allowedFrequencies(template: String): List<String> =
    if (template == "reminder") listOf("once", "daily", "weekly", "monthly") else listOf("daily", "weekly", "monthly")

private fun defaultInterval(template: String): Int = if (template == "hook_mail") 15 else 360

private fun tplLabel(a: AppStrings.SettingsAutomations, tpl: String): String = when (tpl) {
    "briefing" -> a.tplBriefing
    "reminder" -> a.tplOnce
    "proactive_ask" -> a.tplAsk
    "hook_keyword" -> a.tplHookKeyword
    "hook_address" -> a.tplHookAddress
    "hook_mail" -> a.tplHookMail
    else -> tpl
}
private fun tplDesc(a: AppStrings.SettingsAutomations, tpl: String): String = when (tpl) {
    "briefing" -> a.tplBriefingDesc
    "reminder" -> a.tplOnceDesc
    "proactive_ask" -> a.tplAskDesc
    "hook_keyword" -> a.tplHookKeywordDesc
    "hook_address" -> a.tplHookAddressDesc
    "hook_mail" -> a.tplHookMailDesc
    else -> ""
}
private fun freqLabel(a: AppStrings.SettingsAutomations, f: String): String = when (f) {
    "once" -> a.freqOnce
    "daily" -> a.freqDaily
    "weekly" -> a.freqWeekly
    "monthly" -> a.freqMonthly
    else -> f
}

@Composable
fun AutomationBuilder(
    existing: AutomationInfo?,
    agents: List<AutomationAgent>,
    onCancel: () -> Unit,
    onSave: suspend (AutomationDraft) -> String?,
) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val a = t.settingsAutomations
    val scope = rememberCoroutineScope()
    val editing = existing != null

    var template by remember { mutableStateOf(existing?.template) }
    var agentId by remember { mutableStateOf(existing?.agentId ?: "speda") }
    var name by remember { mutableStateOf(existing?.name ?: "") }
    var frequency by remember { mutableStateOf(existing?.schedule?.frequency ?: "daily") }
    var at by remember { mutableStateOf(existing?.schedule?.at ?: "09:00") }
    var days by remember { mutableStateOf(existing?.schedule?.days ?: listOf(1)) }
    var dom by remember { mutableStateOf(existing?.schedule?.dom ?: 1) }
    var date by remember { mutableStateOf(existing?.schedule?.date ?: "") }
    var instruction by remember { mutableStateOf(existing?.instructionRaw ?: existing?.instruction ?: "") }
    var options by remember { mutableStateOf(existing?.options ?: listOf("", "")) }
    var everyMinutes by remember { mutableStateOf(existing?.everyMinutes ?: 5) }
    var maxAsks by remember { mutableStateOf(existing?.maxAsks ?: 10) }
    var dayFlags by remember { mutableStateOf(existing?.dayFlags ?: emptyList()) }
    var voice by remember { mutableStateOf(existing?.voice ?: false) }
    var url by remember { mutableStateOf(existing?.url ?: "") }
    var lookFor by remember { mutableStateOf(existing?.lookFor ?: "") }
    var domain by remember { mutableStateOf(existing?.domain ?: "") }
    var intervalMinutes by remember {
        mutableStateOf(existing?.intervalMinutes ?: existing?.template?.let { defaultInterval(it) } ?: 360)
    }
    // Tracks whether the OWNER set this, as opposed to a template's default
    // still sitting there — an untouched default is always overwritten by a
    // later template switch; an explicit edit never is.
    var intervalTouched by remember { mutableStateOf(existing?.intervalMinutes != null) }

    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    fun chooseTemplate(next: String) {
        template = next
        if (isHook(next)) {
            if (!intervalTouched) intervalMinutes = defaultInterval(next)
        } else {
            val allowed = allowedFrequencies(next)
            if (frequency !in allowed) frequency = allowed.first()
        }
    }

    val cleanOptions = options.map { it.trim() }.filter { it.isNotEmpty() }
    val hookOk = when (template) {
        "hook_keyword" -> url.isNotBlank() && lookFor.isNotBlank()
        "hook_address" -> url.isNotBlank()
        "hook_mail" -> domain.isNotBlank()
        else -> true
    }
    val canSave = template != null && name.isNotBlank() && instruction.isNotBlank() && hookOk &&
        (isHook(template) || frequency != "weekly" || days.isNotEmpty()) &&
        (template != "proactive_ask" || cleanOptions.isNotEmpty()) &&
        dayFlags.all { it.label.isNotBlank() && it.days.isNotEmpty() }

    fun submit() {
        val tpl = template ?: return
        if (!canSave) return
        busy = true
        error = null
        val draft = AutomationDraft(
            agentId = agentId,
            template = tpl,
            name = name.trim(),
            instruction = instruction.trim(),
            // Meaningless for proactive_ask — its reply already goes out
            // through the reminders tool, so composer.py forces it false
            // there regardless.
            voice = if (tpl == "proactive_ask") false else voice,
            schedule = if (!isHook(tpl)) {
                AutomationDraftSchedule(
                    frequency = frequency,
                    at = at,
                    days = if (frequency == "weekly") days else null,
                    dom = if (frequency == "monthly") dom else null,
                    date = if (frequency == "once") date else null,
                )
            } else {
                null
            },
            options = if (tpl == "proactive_ask") cleanOptions else null,
            everyMinutes = if (tpl == "proactive_ask") everyMinutes else null,
            maxAsks = if (tpl == "proactive_ask") maxAsks else null,
            dayFlags = if (!isHook(tpl)) {
                dayFlags.map { it.copy(label = it.label.trim()) }.filter { it.label.isNotEmpty() && it.days.isNotEmpty() }
            } else {
                null
            },
            intervalMinutes = if (isHook(tpl)) intervalMinutes else null,
            domain = if (tpl == "hook_mail") domain.trim() else null,
            url = if (isHook(tpl) && tpl != "hook_mail") url.trim() else null,
            // hook_address fires on ANY change — an explicit empty string
            // clears a stray look_for left over from switching FROM keyword.
            lookFor = if (tpl == "hook_keyword") lookFor.trim() else if (tpl == "hook_address") "" else null,
        )
        scope.launch {
            val err = onSave(draft)
            busy = false
            if (err != null) error = err
        }
    }

    Column(Modifier.fillMaxWidth().padding(bottom = 8.dp)) {
        SectionHeader(if (editing) a.editTitle else a.newTitle, first = true)

        // Step 1 — the kind. Locked in edit mode: a briefing and a proactive
        // ask fire in different output modes, so switching would silently
        // rewrite a live automation's delivery mechanics. Delete and rebuild
        // is the honest path.
        FieldLabel(a.stepType)
        TemplateGrid(SCHEDULE_TEMPLATES, template, editing, a, ::chooseTemplate)
        Spacer(Modifier.height(10.dp))
        FieldLabel(a.stepHookType)
        Hint(a.stepHookTypeHint)
        Spacer(Modifier.height(8.dp))
        TemplateGrid(HOOK_TEMPLATES, template, editing, a, ::chooseTemplate)

        val tpl = template
        if (tpl != null) {
            Spacer(Modifier.height(18.dp))
            FieldLabel(a.nameLabel)
            GlassField(name, { name = it }, placeholder = a.namePlaceholder, singleLine = true)

            Spacer(Modifier.height(14.dp))
            FieldLabel(a.stepAgent)
            Hint(a.stepAgentHint)
            Spacer(Modifier.height(8.dp))
            AgentPicker(agents, agentId) { agentId = it }

            if (isHook(tpl)) {
                SectionHeader(a.stepHookConfig)
                if (tpl == "hook_mail") {
                    FieldLabel(a.mailDomain)
                    Hint(a.mailDomainHint)
                    Spacer(Modifier.height(8.dp))
                    GlassField(domain, { domain = it }, placeholder = a.mailDomainPlaceholder, singleLine = true, mono = true)
                } else {
                    FieldLabel(a.hookUrl)
                    Spacer(Modifier.height(8.dp))
                    GlassField(url, { url = it }, placeholder = "https://…", singleLine = true, mono = true)
                    if (tpl == "hook_keyword") {
                        Spacer(Modifier.height(10.dp))
                        FieldLabel(a.hookKeyword)
                        Hint(a.hookKeywordHint)
                        Spacer(Modifier.height(8.dp))
                        GlassField(lookFor, { lookFor = it }, placeholder = a.hookKeywordPlaceholder, singleLine = true)
                    } else {
                        Spacer(Modifier.height(8.dp))
                        HbText(a.hookAddressNote, style = HbType.readout.copy(fontSize = 11.5.sp), color = palette.textFaint)
                    }
                }
                Spacer(Modifier.height(10.dp))
                FieldLabel(a.checkEvery)
                Hint(a.checkEveryHint)
                Spacer(Modifier.height(8.dp))
                NumberField(intervalMinutes, minValue = 1) { intervalMinutes = it; intervalTouched = true }
            } else {
                SectionHeader(a.stepWhen)
                val freqs = allowedFrequencies(tpl)
                FieldLabel(a.frequency)
                Spacer(Modifier.height(8.dp))
                Row(Modifier.horizontalScroll(androidx.compose.foundation.rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    freqs.forEach { f -> ChoiceChip(freqLabel(a, f), selected = frequency == f) { frequency = f } }
                }
                Spacer(Modifier.height(12.dp))
                FieldLabel(a.time)
                Spacer(Modifier.height(8.dp))
                TimeField(at) { at = it }

                if (frequency == "weekly") {
                    Spacer(Modifier.height(12.dp))
                    FieldLabel(a.weekdays)
                    Spacer(Modifier.height(8.dp))
                    WeekPicker(days, a.dayShort) { days = it }
                }
                if (frequency == "monthly") {
                    Spacer(Modifier.height(12.dp))
                    FieldLabel(a.dayOfMonth)
                    if (dom >= 29) Hint(a.shortMonthWarning)
                    Spacer(Modifier.height(8.dp))
                    NumberField(dom, minValue = 1, maxValue = 31) { dom = it }
                }
                if (frequency == "once") {
                    Spacer(Modifier.height(12.dp))
                    FieldLabel(a.date)
                    Spacer(Modifier.height(8.dp))
                    DateField(date) { date = it }
                }
            }

            SectionHeader(a.stepIntent)
            FieldLabel(a.stepIntent)
            Hint(a.intentHint)
            Spacer(Modifier.height(8.dp))
            GlassField(instruction, { instruction = it }, placeholder = a.intentPlaceholder, singleLine = false, minHeight = 96.dp)

            val polishedInstruction = existing?.let { ex ->
                ex.instruction?.takeIf { ex.intentStatus == "polished" && it.isNotEmpty() && it != ex.instructionRaw }
            }
            if (polishedInstruction != null) {
                Spacer(Modifier.height(10.dp))
                FieldLabel(a.instructionLabel)
                Panel { HbText(polishedInstruction, style = HbType.readout.copy(fontSize = 12.sp), color = palette.textFaint) }
            }

            // Meaningless for proactive_ask — it already delivers through the
            // reminders tool with buttons, not a Telegram audio message.
            if (tpl != "proactive_ask") {
                Spacer(Modifier.height(14.dp))
                SettingsRow(title = a.voiceReply, desc = a.voiceReplyHint) {
                    HbToggle(checked = voice, onToggle = { voice = it })
                }
            }

            if (tpl == "proactive_ask") {
                Spacer(Modifier.height(14.dp))
                FieldLabel(a.answerButtons)
                Hint(a.answerButtonsHint)
                Spacer(Modifier.height(8.dp))
                options.forEachIndexed { i, opt ->
                    Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.weight(1f)) {
                            GlassField(opt, { v -> options = options.mapIndexed { j, o -> if (j == i) v else o } }, placeholder = "", singleLine = true)
                        }
                        SettingsButton("×", onClick = { options = options.filterIndexed { j, _ -> j != i } }, tint = palette.red)
                    }
                }
                SettingsButton(a.addButton, onClick = { options = options + "" })

                Spacer(Modifier.height(14.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    Column(Modifier.weight(1f)) {
                        FieldLabel(a.repeatEvery)
                        NumberField(everyMinutes, minValue = 1) { everyMinutes = it }
                    }
                    Column(Modifier.weight(1f)) {
                        FieldLabel(a.maxAsks)
                        NumberField(maxAsks, minValue = 1) { maxAsks = it }
                    }
                }
            }

            if (!isHook(tpl)) {
                Spacer(Modifier.height(14.dp))
                FieldLabel(a.dayFlags)
                Hint(a.dayFlagsHint)
                Spacer(Modifier.height(8.dp))
                dayFlags.forEachIndexed { i, flag ->
                    Column(
                        Modifier
                            .fillMaxWidth()
                            .padding(bottom = 10.dp)
                            .clip(RoundedCornerShape(6.dp))
                            .background(palette.text.copy(alpha = 0.03f))
                            .padding(12.dp),
                    ) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.weight(1f)) {
                                GlassField(
                                    flag.label,
                                    { v -> dayFlags = dayFlags.mapIndexed { j, f -> if (j == i) f.copy(label = v) else f } },
                                    placeholder = a.flagLabelPlaceholder, singleLine = true,
                                )
                            }
                            SettingsButton("×", onClick = { dayFlags = dayFlags.filterIndexed { j, _ -> j != i } }, tint = palette.red)
                        }
                        Spacer(Modifier.height(8.dp))
                        WeekPicker(flag.days, a.dayShort) { v -> dayFlags = dayFlags.mapIndexed { j, f -> if (j == i) f.copy(days = v) else f } }
                    }
                }
                SettingsButton(a.addFlag, onClick = { dayFlags = dayFlags + AutomationDayFlag("", emptyList()) })
            }

            error?.let {
                Spacer(Modifier.height(14.dp))
                HbText(it, style = HbType.readout.copy(fontSize = 11.5.sp), color = palette.red)
            }

            Spacer(Modifier.height(16.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                SettingsButton(
                    if (busy) a.saving else if (editing) a.save else a.create,
                    enabled = canSave && !busy,
                    onClick = ::submit,
                )
                SettingsButton(a.cancel, onClick = onCancel, tint = palette.textDim)
            }
        }
    }
}

@Composable
private fun TemplateGrid(templates: List<String>, template: String?, editing: Boolean, a: AppStrings.SettingsAutomations, onPick: (String) -> Unit) {
    val palette = LocalHbPalette.current
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        templates.forEach { tpl ->
            val on = template == tpl
            val locked = editing && !on
            Column(
                Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(8.dp))
                    .background(if (on) palette.accent.copy(alpha = 0.1f) else palette.text.copy(alpha = 0.03f))
                    .border(1.dp, if (on) palette.accent.copy(alpha = 0.34f) else palette.text.copy(alpha = 0.08f), RoundedCornerShape(8.dp))
                    .then(if (!editing) Modifier.clickable { onPick(tpl) } else Modifier)
                    .padding(horizontal = 14.dp, vertical = 12.dp),
            ) {
                HbText(tplLabel(a, tpl), style = HbType.read.copy(fontSize = 14.5.sp), color = if (on) palette.accentBright else if (locked) palette.textFaint else palette.text)
                Spacer(Modifier.height(3.dp))
                HbText(tplDesc(a, tpl), style = HbType.readout.copy(fontSize = 11.5.sp), color = palette.textFaint)
            }
        }
    }
}

@Composable
private fun ChoiceChip(label: String, selected: Boolean, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Box(
        Modifier
            .clip(RoundedCornerShape(50))
            .background(if (selected) palette.accent.copy(alpha = 0.14f) else palette.text.copy(alpha = 0.03f))
            .border(1.dp, if (selected) palette.accent.copy(alpha = 0.34f) else palette.text.copy(alpha = 0.1f), RoundedCornerShape(50))
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 7.dp),
    ) {
        HbText(label, style = HbType.read.copy(fontSize = 13.sp), color = if (selected) palette.accentBright else palette.textDim)
    }
}

@Composable
private fun WeekPicker(value: List<Int>, labels: List<String>, onChange: (List<Int>) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        WEEK.forEach { d ->
            val on = d in value
            ChoiceChip(labels[d - 1], selected = on) {
                onChange(if (on) value.filter { it != d } else (value + d).sorted())
            }
        }
    }
}

@Composable
private fun AgentPicker(agents: List<AutomationAgent>, selected: String, onSelect: (String) -> Unit) {
    Row(Modifier.horizontalScroll(androidx.compose.foundation.rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        agents.forEach { ag ->
            ChoiceChip("${ag.name} — ${ag.domain}", selected = selected == ag.agentId) { onSelect(ag.agentId) }
        }
    }
}

@Composable
private fun NumberField(value: Int, minValue: Int = 0, maxValue: Int = Int.MAX_VALUE, onChange: (Int) -> Unit) {
    GlassField(
        value.toString(),
        { raw ->
            val digits = raw.filter { it.isDigit() }
            if (digits.isEmpty()) onChange(minValue)
            else onChange(digits.toInt().coerceIn(minValue, maxValue))
        },
        placeholder = "",
        singleLine = true,
        mono = true,
    )
}

/** A GlassField-shaped tappable row — same visual weight as the text fields
 *  around it, but opens a picker instead of a keyboard. */
@Composable
private fun PickerField(label: String) {
    val palette = LocalHbPalette.current
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(palette.text.copy(alpha = 0.03f))
            .border(1.dp, palette.text.copy(alpha = 0.09f), RoundedCornerShape(8.dp))
            .padding(horizontal = 16.dp, vertical = 12.dp),
    ) {
        HbText(label, style = HbType.read.copy(fontSize = 15.sp), color = if (label.isEmpty()) palette.textFaint else palette.text)
    }
}

/** `at` — a 24-hour time, picked with Material3's TimePicker in a small glass
 *  dialog. Forced 24-hour: the backend's AutomationSchedule.at is 'HH:MM' with
 *  no AM/PM concept, and letting the picker default to the locale's 12-hour
 *  face would round-trip through an ambiguous hour on some devices. */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun TimeField(value: String, onChange: (String) -> Unit) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    var open by remember { mutableStateOf(false) }

    Box(Modifier.clickable { open = true }) { PickerField(value.ifBlank { "HH:MM" }) }

    if (open) {
        val parts = remember(value) { value.split(":") }
        val initHour = parts.getOrNull(0)?.toIntOrNull()?.coerceIn(0, 23) ?: 9
        val initMinute = parts.getOrNull(1)?.toIntOrNull()?.coerceIn(0, 59) ?: 0
        val state = androidx.compose.material3.rememberTimePickerState(
            initialHour = initHour, initialMinute = initMinute, is24Hour = true,
        )
        Dialog(onDismissRequest = { open = false }) {
            Column(
                Modifier.hbGlass(shape = HbGlassShape.Card).padding(20.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                androidx.compose.material3.TimePicker(state = state)
                Spacer(Modifier.height(14.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    SettingsButton(t.common.cancel, onClick = { open = false }, tint = palette.textDim)
                    SettingsButton(
                        t.common.ok,
                        onClick = {
                            onChange("%02d:%02d".format(state.hour, state.minute))
                            open = false
                        },
                    )
                }
            }
        }
    }
}

/**
 * `date` — a calendar day for the "once" frequency, picked with Material3's
 * DatePicker. [SelectableDates] floors selection at today (UTC midnight,
 * matching DatePicker's own convention) for the same reason the desktop input
 * carries `min={todayISO()}`: a date already gone compiles to a workflow that
 * is live, green, and can never fire.
 */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun DateField(value: String, onChange: (String) -> Unit) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    var open by remember { mutableStateOf(false) }

    Box(Modifier.clickable { open = true }) { PickerField(value.ifBlank { "YYYY-MM-DD" }) }

    if (open) {
        val todayMillis = remember {
            java.time.LocalDate.now(java.time.ZoneOffset.UTC)
                .atStartOfDay(java.time.ZoneOffset.UTC).toInstant().toEpochMilli()
        }
        val initMillis = remember(value) {
            runCatching {
                java.time.LocalDate.parse(value)
                    .atStartOfDay(java.time.ZoneOffset.UTC).toInstant().toEpochMilli()
            }.getOrNull()
        }
        val state = androidx.compose.material3.rememberDatePickerState(
            initialSelectedDateMillis = initMillis,
            selectableDates = object : androidx.compose.material3.SelectableDates {
                override fun isSelectableDate(utcTimeMillis: Long): Boolean = utcTimeMillis >= todayMillis
            },
        )
        androidx.compose.material3.DatePickerDialog(
            onDismissRequest = { open = false },
            confirmButton = {
                SettingsButton(
                    t.common.ok,
                    onClick = {
                        state.selectedDateMillis?.let { millis ->
                            onChange(
                                java.time.Instant.ofEpochMilli(millis).atZone(java.time.ZoneOffset.UTC).toLocalDate().toString(),
                            )
                        }
                        open = false
                    },
                )
            },
            dismissButton = { SettingsButton(t.common.cancel, onClick = { open = false }, tint = palette.textDim) },
        ) {
            androidx.compose.material3.DatePicker(state = state)
        }
    }
}
