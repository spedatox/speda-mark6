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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.DoormatState
import com.speda.heartbreaker.data.LifeboatState
import com.speda.heartbreaker.data.LockdownState
import com.speda.heartbreaker.data.OctaviusState
import com.speda.heartbreaker.data.SkyfallArm
import com.speda.heartbreaker.data.SkyfallProject
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.launch

/**
 * PROTOCOLS, on the phone — the same six the desktop shows, and one of them is
 * shown precisely so it can say it is not available here.
 *
 *   Lockdown    engage (passphrase) / stand down — both work from the phone
 *   Lifeboat    read-only; reclamation is owner-led THROUGH Orion
 *   Octavius    read-only plus "back up now", which can only create
 *   Doormat     read-only; a domain move is a conversation, not a form
 *   Skyfall     the launch rail — pick a project, get the countdown
 *   House Party DESKTOP ONLY, and visibly so
 *
 * WHY HOUSE PARTY IS HERE AT ALL. It stages the whole roster in a war room the
 * phone does not build, so the backend refuses to engage it from this surface
 * (app/core/surface.py). Leaving it out would be the wrong kind of tidy: the
 * owner would wonder whether the protocol still exists. Showing it greyed, with
 * the reason, answers the question the omission would have raised.
 *
 * WHY THE OTHERS ARE READ-ONLY. Each has a gate in its skill requiring the owner
 * in the conversation. A button here would be a second path around that gate.
 * What a pane is better at than a chat message is showing STATE you want to look
 * at while doing something else — a disk figure, a backup age, the console
 * checklist you are working through — and that is what these carry.
 *
 * EVERY ROW READS `reachable` FIRST. An unreachable server is not evidence that
 * a protocol is switched off, and saying "disabled on this deployment" about a
 * server nobody managed to ask sends the owner to change a setting that was
 * never the problem.
 */
@Composable
fun ProtocolsTab(
    config: AppConfig,
    graph: AppGraph,
    onArmSkyfall: (SkyfallArm) -> Unit,
) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val p = t.protocols
    val scope = rememberCoroutineScope()

    var lockdown by remember { mutableStateOf<LockdownState?>(null) }
    var lifeboat by remember { mutableStateOf<LifeboatState?>(null) }
    var octavius by remember { mutableStateOf<OctaviusState?>(null) }
    var doormat by remember { mutableStateOf<DoormatState?>(null) }
    var projects by remember { mutableStateOf<List<SkyfallProject>?>(null) }
    var busy by remember { mutableStateOf(false) }
    var note by remember { mutableStateOf("") }

    suspend fun refresh() {
        lockdown = graph.api.fetchLockdown(config)
        lifeboat = graph.api.fetchLifeboat(config)
        octavius = graph.api.fetchOctavius(config)
        doormat = graph.api.fetchDoormat(config)
        projects = graph.api.fetchSkyfallProjects(config)
    }

    LaunchedEffect(config) { refresh() }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 40.dp),
    ) {
        // ── LOCKDOWN ────────────────────────────────────────────────────────
        SectionHeader(p.lockdownTitle, first = true)
        Hint(p.lockdownDesc)
        Spacer(Modifier.height(12.dp))
        val lock = lockdown
        val sealed = lock?.rules?.values?.any { it } == true
        SettingsRow(
            title = when {
                lock == null -> p.loading
                !lock.reachable -> p.unreachable
                lock.engaged -> p.containmentActive
                else -> p.containmentInactive
            },
            desc = when {
                lock == null -> ""
                !lock.reachable -> p.unreachableHint
                !lock.enabled -> p.lockdownDisabled
                lock.engaged -> p.portsSealed
                else -> p.acceptingNormally
            },
        ) {
            // Offered whenever the flag says contained OR a rule is actually in
            // place: engage() applies the firewall rules BEFORE it persists the
            // flag, so a request that dies in between leaves the ports sealed
            // with the flag reading off — the one state that most needs the way
            // out. disengage() is ungated and removes rules unconditionally.
            if (lock != null && lock.reachable && (lock.engaged || sealed)) {
                SettingsButton(
                    p.standDown,
                    onClick = {
                        busy = true
                        scope.launch {
                            graph.api.standDownLockdown(config)
                            refresh()
                            note = p.containmentStoodDown
                            busy = false
                        }
                    },
                    enabled = !busy,
                    tint = palette.red,
                )
            }
        }
        if (lock != null && lock.rules.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Readout(p.firewallRules) {
                lock.rules.forEach { (label, on) ->
                    ReadoutLine(label, if (on) p.sealedLabel else p.openLabel,
                        if (on) Color(0xFFE5897C) else palette.textFaint)
                }
            }
        }

        // ── LIFEBOAT ────────────────────────────────────────────────────────
        SectionHeader(p.lifeboatTitle)
        Hint(p.lifeboatDesc)
        Spacer(Modifier.height(12.dp))
        val boat = lifeboat
        SettingsRow(
            title = when {
                boat == null -> p.loading
                !boat.reachable -> p.unreachable
                boat.status == "disabled" -> p.notEnabled
                boat.status == "error" -> p.hostUnreadable
                else -> p.lifeboatLevel(boat.level)
            },
            desc = when {
                boat == null -> ""
                !boat.reachable -> p.unreachableHint
                boat.status == "disabled" -> p.lifeboatDisabled
                boat.status == "error" -> boat.detail.ifBlank { p.hostUnreadableHint }
                else -> boat.summary.ifBlank { p.hostHealthy }
            },
        ) {
            HbText(p.throughOrion, style = HbType.read.copy(fontSize = 13.5.sp), color = palette.textFaint)
        }
        if (boat != null && boat.status == "ok") {
            Spacer(Modifier.height(10.dp))
            Readout(if (boat.pressed.isEmpty()) p.hostResources else p.underPressure) {
                ReadoutLine(
                    "disk",
                    "${fmt(boat.readings.diskPct)}%  ·  ${fmt(boat.readings.diskFreeGb)} GB",
                    levelColor(boat.byResource["disk"], palette.textFaint),
                )
                ReadoutLine("inodes", "${fmt(boat.readings.inodePct)}%",
                    levelColor(boat.byResource["inodes"], palette.textFaint))
                ReadoutLine(
                    "memory",
                    "${fmt(boat.readings.memPct)}%  ·  ${fmt(boat.readings.memAvailableGb)} GB",
                    levelColor(boat.byResource["memory"], palette.textFaint),
                )
                ReadoutLine("docker reclaimable", "${fmt(boat.readings.dockerReclaimableGb)} GB",
                    palette.textFaint)
            }
        }

        // ── OCTAVIUS ────────────────────────────────────────────────────────
        SectionHeader(p.octaviusTitle)
        Hint(p.octaviusDesc)
        Spacer(Modifier.height(12.dp))
        val arc = octavius
        SettingsRow(
            title = when {
                arc == null -> p.loading
                !arc.reachable -> p.unreachable
                !arc.enabled -> p.notEnabled
                arc.count == 0 -> p.noBackups
                arc.stale -> p.backupsStale
                else -> p.backupsHealthy
            },
            desc = when {
                arc == null -> ""
                !arc.reachable -> p.unreachableHint
                !arc.enabled -> p.octaviusDisabled
                arc.latest != null -> p.backupLatest(arc.latest.name, arc.latest.mb, arc.ageHours, arc.count)
                else -> arc.detail.ifBlank { p.nothingToRestoreFrom }
            },
        ) {
            if (arc != null && arc.reachable && arc.enabled) {
                // The one action worth a button: it can only create. The worst a
                // stray press does is spend some bandwidth and add a file.
                SettingsButton(
                    if (busy) p.backingUp else p.backupNow,
                    onClick = {
                        busy = true
                        scope.launch {
                            val ok = graph.api.runOctaviusBackup(config)
                            refresh()
                            note = if (ok) p.backupDone else p.backupFailed
                            busy = false
                        }
                    },
                    enabled = !busy,
                )
            }
        }

        // ── DOORMAT ─────────────────────────────────────────────────────────
        SectionHeader(p.doormatTitle)
        Hint(p.doormatDesc)
        Spacer(Modifier.height(12.dp))
        val door = doormat
        SettingsRow(
            title = when {
                door == null -> p.loading
                !door.reachable -> p.unreachable
                !door.enabled -> p.notEnabled
                door.phase == "staged" -> p.movePreparing
                door.phase == "cutover" -> p.moveCutOver
                else -> p.noMoveInProgress
            },
            desc = when {
                door == null -> ""
                !door.reachable -> p.unreachableHint
                !door.enabled -> p.doormatDisabled
                door.phase.isNotEmpty() ->
                    "${door.previous.ifBlank { door.currentDomain }} → ${door.target}"
                else -> p.servingDomain(door.currentDomain.ifBlank { "—" })
            },
        ) {
            HbText(p.throughOrion, style = HbType.read.copy(fontSize = 13.5.sp), color = palette.textFaint)
        }
        if (door != null && door.restartPending) {
            Spacer(Modifier.height(10.dp))
            Readout(p.restartOutstanding, alarm = true) {
                HbText(p.restartOutstandingHint,
                    style = HbType.read.copy(fontSize = 12.5.sp), color = palette.textDim)
            }
        }
        // The reason this section is worth a pane at all: the console steps are
        // something the owner sits and works through, not something to ask an
        // agent to re-print each time one is finished.
        if (door != null && door.phase == "staged" && door.checklist.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Readout(p.consoleChecklist) {
                HbText(p.addDoNotReplace,
                    style = HbType.read.copy(fontSize = 12.5.sp), color = palette.textDim)
                door.checklist.forEachIndexed { i, step ->
                    Spacer(Modifier.height(8.dp))
                    HbText("${i + 1}. ${step.provider}",
                        style = HbType.code.copy(fontSize = 11.5.sp), color = palette.text)
                    HbText(step.where, style = HbType.code.copy(fontSize = 11.sp), color = palette.textFaint)
                    HbText("${step.field}: ${step.value}",
                        style = HbType.code.copy(fontSize = 11.sp), color = palette.accentBright)
                }
            }
        }

        // ── SKYFALL ─────────────────────────────────────────────────────────
        SectionHeader(p.skyfallTitle)
        Hint(p.skyfallDesc)
        Spacer(Modifier.height(12.dp))
        SkyfallSection(
            config = config,
            graph = graph,
            projects = projects,
            onReload = { scope.launch { projects = graph.api.fetchSkyfallProjects(config) } },
            onArm = onArmSkyfall,
        )

        // ── HOUSE PARTY — present so it can say it is not here ──────────────
        SectionHeader(p.housePartyTitle)
        Hint(p.housePartyDesc)
        Spacer(Modifier.height(12.dp))
        SettingsRow(title = p.desktopOnly, desc = p.housePartyPhoneHint) {
            HbText(p.desktopOnlyTag, style = HbType.read.copy(fontSize = 13.5.sp),
                color = palette.textFaint)
        }

        if (note.isNotBlank()) {
            Spacer(Modifier.height(14.dp))
            Readout(p.lastAction) {
                HbText(note, style = HbType.code.copy(fontSize = 11.5.sp), color = palette.textDim)
            }
        }
    }
}

private fun fmt(value: Double?): String =
    if (value == null || value < 0) "?" else if (value % 1.0 == 0.0) value.toInt().toString()
    else String.format("%.1f", value)

@Composable
private fun levelColor(level: String?, fallback: Color): Color = when (level) {
    "critical" -> Color(0xFFE5897C)
    "watch" -> Color(0xFFD9A441)
    else -> fallback
}

/** A mono readout of what the machine actually reports, under a small caption. */
@Composable
private fun Readout(title: String, alarm: Boolean = false, content: @Composable () -> Unit) {
    val palette = LocalHbPalette.current
    val rim = if (alarm) Color(0xFFD8483C).copy(alpha = 0.35f) else Color.White.copy(alpha = 0.07f)
    Column(
        Modifier
            .fillMaxWidth()
            .background(if (alarm) Color(0xFFD8483C).copy(alpha = 0.08f) else Color.White.copy(alpha = 0.03f))
            .border(1.dp, rim)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        HbText(title, style = HbType.label.copy(fontSize = 9.5.sp, letterSpacing = 2.sp),
            color = if (alarm) Color(0xFFE5897C) else palette.textFaint, caps = true)
        content()
    }
}

@Composable
private fun ReadoutLine(left: String, right: String, tint: Color) {
    val palette = LocalHbPalette.current
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        HbText(left, style = HbType.code.copy(fontSize = 11.5.sp), color = palette.textDim)
        HbText(right, style = HbType.code.copy(fontSize = 11.5.sp), color = tint)
    }
}

/**
 * The launch rail: the project list, and the form that writes one.
 *
 * Arming does NOT open the countdown here. It fetches the arming payload and
 * hands it up to the shell, which owns the one screen both routes land on —
 * building a second countdown inside this pane is exactly how a protocol ends
 * up with a path that skips its own safety.
 */
@Composable
private fun SkyfallSection(
    config: AppConfig,
    graph: AppGraph,
    projects: List<SkyfallProject>?,
    onReload: () -> Unit,
    onArm: (SkyfallArm) -> Unit,
) {
    val palette = LocalHbPalette.current
    val p = LocalStrings.current.protocols
    val scope = rememberCoroutineScope()
    var editing by remember { mutableStateOf<SkyfallProject?>(null) }
    var error by remember { mutableStateOf("") }

    val draft = editing
    if (draft != null) {
        SkyfallForm(
            draft = draft,
            error = error,
            onChange = { editing = it },
            onCancel = { editing = null; error = "" },
            onSave = {
                scope.launch {
                    val problem = graph.api.saveSkyfallProject(config, draft)
                    if (problem == null) { editing = null; error = ""; onReload() } else error = problem
                }
            },
        )
        return
    }

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        when {
            projects == null -> HbText(p.loading,
                style = HbType.read.copy(fontSize = 13.sp), color = palette.textFaint)
            projects.isEmpty() -> Box(
                Modifier.fillMaxWidth()
                    .background(Color.White.copy(alpha = 0.03f))
                    .border(1.dp, Color.White.copy(alpha = 0.12f))
                    .padding(18.dp),
                contentAlignment = Alignment.Center,
            ) {
                HbText(p.skyfallEmpty, style = HbType.read.copy(fontSize = 13.sp), color = palette.textFaint)
            }
            else -> projects.forEach { project ->
                Row(
                    Modifier.fillMaxWidth()
                        .background(Color(0xFFD8483C).copy(alpha = 0.06f))
                        .border(1.dp, Color(0xFFD8483C).copy(alpha = 0.22f))
                        .padding(horizontal = 14.dp, vertical = 14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Box(Modifier.width(3.dp).height(46.dp).background(Color(0xFFD8483C)))
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        HbText(project.name, style = HbType.read.copy(fontSize = 15.sp), color = palette.text)
                        if (project.description.isNotBlank()) {
                            HbText(project.description,
                                style = HbType.read.copy(fontSize = 13.sp), color = palette.textFaint)
                        }
                        HbText(
                            "${project.method} ${project.url} · ${p.seconds(project.countdownSeconds)}",
                            style = HbType.code.copy(fontSize = 10.5.sp), color = palette.textDim,
                        )
                    }
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        SettingsButton(
                            p.arm,
                            onClick = {
                                scope.launch {
                                    val payload = graph.api.armSkyfall(config, project.id)
                                    if (payload != null) onArm(payload) else error = p.armFailed
                                }
                            },
                            tint = Color(0xFFE5897C),
                        )
                        SettingsButton(p.edit, onClick = { editing = project })
                        SettingsButton(
                            p.delete,
                            onClick = {
                                scope.launch {
                                    graph.api.deleteSkyfallProject(config, project.id)
                                    onReload()
                                }
                            },
                        )
                    }
                }
            }
        }

        if (error.isNotBlank()) {
            HbText(error, style = HbType.code.copy(fontSize = 11.5.sp), color = Color(0xFFE5897C))
        }
        SettingsButton(
            p.addProject,
            onClick = { editing = SkyfallProject(method = "POST", countdownSeconds = 10) },
        )
    }
}

/**
 * The form. Headers are edited as raw `Name: value` lines rather than a row
 * builder — one text field round-trips a masked secret unchanged, and a row
 * builder is where a "clear" button ends up wiping a token by accident.
 */
@Composable
private fun SkyfallForm(
    draft: SkyfallProject,
    error: String,
    onChange: (SkyfallProject) -> Unit,
    onCancel: () -> Unit,
    onSave: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val p = LocalStrings.current.protocols
    val headerText = draft.headers.entries.joinToString("\n") { "${it.key}: ${it.value}" }

    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        FieldLabel(p.fieldName)
        GlassField(draft.name, { onChange(draft.copy(name = it)) }, p.namePlaceholder, singleLine = true)

        FieldLabel(p.fieldDescription)
        Hint(p.descriptionHint)
        GlassField(draft.description, { onChange(draft.copy(description = it)) }, "", singleLine = true)

        FieldLabel(p.fieldMethod)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("GET", "POST", "PUT", "PATCH", "DELETE").forEach { m ->
                val on = draft.method == m
                Box(
                    Modifier
                        .background(if (on) palette.accent.copy(alpha = 0.16f) else Color.White.copy(alpha = 0.04f))
                        .border(1.dp, if (on) palette.accent.copy(alpha = 0.4f) else Color.White.copy(alpha = 0.1f))
                        .clickable { onChange(draft.copy(method = m)) }
                        .padding(horizontal = 10.dp, vertical = 7.dp),
                ) {
                    HbText(m, style = HbType.code.copy(fontSize = 11.sp),
                        color = if (on) palette.accentBright else palette.textDim)
                }
            }
        }

        FieldLabel(p.fieldUrl)
        GlassField(draft.url, { onChange(draft.copy(url = it)) }, "https://…", singleLine = true, mono = true)

        FieldLabel(p.fieldCountdown)
        Hint(p.countdownHint)
        GlassField(
            draft.countdownSeconds.toString(),
            { onChange(draft.copy(countdownSeconds = it.filter(Char::isDigit).toIntOrNull() ?: 0)) },
            "10", singleLine = true, mono = true,
        )

        FieldLabel(p.fieldBody)
        Hint(p.bodyHint)
        GlassField(draft.body, { onChange(draft.copy(body = it)) }, "{ }",
            singleLine = false, minHeight = 90.dp, mono = true)

        FieldLabel(p.fieldHeaders)
        Hint(p.headersHint)
        GlassField(
            headerText,
            { text ->
                val parsed = buildMap {
                    text.lines().forEach { line ->
                        val at = line.indexOf(':')
                        if (at > 0) {
                            val name = line.take(at).trim()
                            if (name.isNotEmpty()) put(name, line.drop(at + 1).trim())
                        }
                    }
                }
                onChange(draft.copy(headers = parsed))
            },
            "Authorization: Bearer …", singleLine = false, minHeight = 70.dp, mono = true,
        )

        if (error.isNotBlank()) {
            HbText(error, style = HbType.code.copy(fontSize = 11.5.sp), color = Color(0xFFE5897C))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            SettingsButton(p.save, onClick = onSave, tint = palette.accent)
            SettingsButton(p.cancel, onClick = onCancel)
        }
    }
}
