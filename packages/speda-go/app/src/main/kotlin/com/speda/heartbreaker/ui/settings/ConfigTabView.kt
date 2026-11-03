// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.settings

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.ConfigFieldInfo
import com.speda.heartbreaker.data.ConfigGroupInfo
import com.speda.heartbreaker.data.ConfigSaveResult
import com.speda.heartbreaker.data.MemorySources
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.AppStrings
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.jsonPrimitive

/**
 * Configuration tab — the full backend config surface (every API key, bot token,
 * endpoint and feature flag), grouped and collapsible, with dirty-delta save.
 * A faithful port of ConfigTab.tsx: reads GET /config (secrets masked to a hint),
 * tracks only DIRTY fields, PUTs the delta, and reports applied-live vs
 * restart-required. Plus the per-agent Source-of-Truth memory assignment.
 */
@Composable
fun ConfigTabView(config: AppConfig, graph: AppGraph) {
    val scope = rememberCoroutineScope()
    val t = LocalStrings.current
    val tr = t == com.speda.heartbreaker.i18n.Tr

    var groups by remember { mutableStateOf<List<ConfigGroupInfo>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var query by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<ConfigSaveResult?>(null) }
    var revision by remember { mutableStateOf<Int?>(null) }
    var instance by remember { mutableStateOf("") }
    var canRestart by remember { mutableStateOf(false) }
    var needsRestart by remember { mutableStateOf(false) }
    var restarting by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var notice by remember { mutableStateOf("") }
    var saveId by remember { mutableStateOf<String?>(null) }
    var restartId by remember { mutableStateOf<String?>(null) }
    val resets = remember { mutableStateMapOf<String, Boolean>() }

    // Only DIRTY fields live here; the value is already the wire JsonElement.
    val edits = remember { mutableStateMapOf<String, JsonElement>() }
    val open = remember { mutableStateMapOf<String, Boolean>() }
    val reveal = remember { mutableStateMapOf<String, Boolean>() }

    suspend fun load() {
        loading = true
        try {
            val snapshot = graph.api.getConfigSnapshot(config)
            groups = snapshot.groups; revision = snapshot.revision; instance = snapshot.instanceId
            canRestart = snapshot.restart.available; needsRestart = snapshot.restartRequired.isNotEmpty()
            if (open.isEmpty() && groups.isNotEmpty()) open[groups.first().id] = true
            error = ""
        } catch (e: Exception) { error = e.message ?: "Configuration unavailable" }
        finally { loading = false }
    }
    LaunchedEffect(config) { load() }

    val q = query.trim().lowercase()
    val filtered = remember(groups, q) {
        if (q.isEmpty()) groups
        else groups.mapNotNull { g ->
            val fields = g.fields.filter {
                it.label.lowercase().contains(q) || it.key.lowercase().contains(q) || g.label.lowercase().contains(q)
            }
            if (fields.isEmpty()) null else g.copy(fields = fields)
        }
    }

    Column(Modifier.fillMaxSize()) {
        Column(
            Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 4.dp),
        ) {
            SectionHeader(t.settingsTabs.config.label)
            Hint(if (tr) "Kaydedilen sunucu ayarları yeniden başlatmada etkinleşir. Boş anahtar kapatır; geçersiz kılmayı sıfırlamak dağıtım değerine döner." else "Saved server settings apply together on restart. Empty credentials disable them; resetting an override restores the deployment value.")
            if (error.isNotEmpty()) Hint(error)
            if (notice.isNotEmpty()) Hint(notice)
            SettingsButton(if (tr) "Kaydedilen ayarları yeniden yükle" else "Reload saved settings (keep draft)", enabled = !saving && !restarting, onClick = { scope.launch { load() } })
            if (!canRestart && !loading) Hint(if (tr) "Yeniden başlatma için sunucuyu python -m app.supervisor ile başlatın." else "Start the server with python -m app.supervisor to enable restart.")
            Spacer(Modifier.height(12.dp))

            SourceOfTruthPanel(config, graph)
            ConfigPoliciesView(config, graph)
            Spacer(Modifier.height(12.dp))

            if (loading) {
                Hint(t.settingsConfig.loading)
            } else {
                // Search
                GlassField(
                    value = query,
                    onValueChange = { query = it },
                    placeholder = t.settingsConfig.searchPlaceholder,
                    singleLine = true,
                )
                Spacer(Modifier.height(12.dp))

                filtered.forEach { g ->
                    val isOpen = open[g.id] == true || q.isNotEmpty()
                    val groupDirty = g.fields.count { it.key in edits }
                    ConfigGroup(
                        group = g,
                        isOpen = isOpen,
                        dirtyCount = groupDirty,
                        onToggle = { open[g.id] = !(open[g.id] == true) },
                        edits = edits,
                        resets = resets,
                        editable = !saving && !restarting,
                        onEdited = { saveId = null },
                        reveal = reveal,
                        t = t,
                    )
                    Spacer(Modifier.height(10.dp))
                }
                Spacer(Modifier.height(12.dp))
            }
        }

        // Save bar — pinned to the bottom of the tab (the web's sticky footer).
        if (!loading) {
            SaveBar(
                dirtyCount = (edits.keys + resets.keys).size,
                saving = saving || restarting,
                result = result,
                onDiscard = { edits.clear(); resets.clear(); saveId = null; result = null },
                onSave = {
                    if ((edits.isNotEmpty() || resets.isNotEmpty()) && !saving && !restarting && revision != null) {
                        scope.launch {
                            saving = true; result = null
                            try {
                                val id = saveId ?: java.util.UUID.randomUUID().toString().also { saveId = it }
                                result = graph.api.saveConfig(config, edits.toMap(), revision!!, id, resets.keys.toList())
                                edits.clear(); resets.clear(); saveId = null; restartId = null
                                load()
                            } catch (e: Exception) { error = e.message ?: "Configuration save failed" }
                            finally { saving = false }
                        }
                    }
                },
                t = t,
            )
            if (needsRestart || result != null) SettingsButton(
                if (restarting) (if (tr) "Yeniden başlatılıyor…" else "Restarting…") else (if (tr) "Arka ucu yeniden başlat" else "Restart backend"),
                enabled = canRestart && edits.isEmpty() && resets.isEmpty() && !saving && !restarting,
                onClick = {
                    scope.launch {
                        restarting = true; error = ""
                        notice = if (tr) "Yeni arka uç bekleniyor…" else "Waiting for the new backend…"
                        try {
                            val id = restartId ?: java.util.UUID.randomUUID().toString().also { restartId = it }
                            graph.api.restartBackend(config, revision!!, id)
                            graph.api.waitForBackend(config, instance)
                            notice = if (tr) "Arka uç yeniden başladı. API anahtarı veya adres değiştiyse bağlantıyı güncelleyin." else "Backend restarted. Update connection settings if its API key or address changed."
                            restartId = null; result = null; load()
                        } catch (e: Exception) { notice = ""; error = e.message ?: "Restart was not confirmed" }
                        finally { restarting = false }
                    }
                },
            )
        }
    }
}

@Composable
private fun ConfigGroup(
    group: ConfigGroupInfo,
    isOpen: Boolean,
    dirtyCount: Int,
    onToggle: () -> Unit,
    edits: MutableMap<String, JsonElement>,
    resets: MutableMap<String, Boolean>,
    editable: Boolean,
    onEdited: () -> Unit,
    reveal: MutableMap<String, Boolean>,
    t: AppStrings,
) {
    val palette = LocalHbPalette.current
    Column(Modifier.fillMaxWidth().hbGlass(shape = HbGlassShape.Ctl)) {
        Row(
            Modifier.fillMaxWidth().clickable(onClick = onToggle).padding(horizontal = 12.dp, vertical = 11.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(9.dp),
        ) {
            // ChevronDown rotated: points right when collapsed, down when open.
            val angle by animateFloatAsState(if (isOpen) 0f else -90f, label = "chev")
            HbGlyphs.ChevronDown(palette.accent, size = 11.dp, modifier = Modifier.rotate(angle))
            Column(Modifier.weight(1f)) {
                HbText(group.label, style = HbType.read.copy(fontSize = 14.sp, fontWeight = FontWeight.Bold), color = palette.text)
                if (group.blurb.isNotEmpty()) {
                    HbText(group.blurb, style = HbType.readout.copy(fontSize = 11.sp, lineHeight = 1.4.em), color = palette.textFaint)
                }
            }
            if (dirtyCount > 0) {
                Box(
                    Modifier.border(1.dp, palette.amber.copy(alpha = 0.5f)).padding(horizontal = 6.dp, vertical = 2.dp),
                ) {
                    HbText(t.settingsConfig.groupEdited(dirtyCount), style = HbType.readout.copy(fontSize = 9.sp), color = palette.amber)
                }
            }
        }
        if (isOpen) {
            Column(
                Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, bottom = 12.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                group.fields.forEach { f ->
                    ConfigFieldRow(
                        f = f,
                        edit = edits[f.key],
                        dirty = f.key in edits,
                        revealed = reveal[f.key] == true,
                        onReveal = { reveal[f.key] = !(reveal[f.key] == true) },
                        onChange = { if (editable) { onEdited(); resets.remove(f.key); edits[f.key] = it } },
                        onReset = { if (editable) { onEdited(); edits.remove(f.key); resets.remove(f.key) } },
                        t = t,
                    )
                    if (f.overridden) SettingsButton(
                        if (t == com.speda.heartbreaker.i18n.Tr) (if (resets[f.key] == true) "Kaydetmede sıfırlanacak" else "Kayıtlı geçersiz kılmayı sıfırla") else (if (resets[f.key] == true) "Will reset on save" else "Reset saved override"),
                        enabled = editable,
                        onClick = { onEdited(); edits.remove(f.key); resets[f.key] = true },
                    )
                }
            }
        }
    }
}

@Composable
private fun ConfigFieldRow(
    f: ConfigFieldInfo,
    edit: JsonElement?,
    dirty: Boolean,
    revealed: Boolean,
    onReveal: () -> Unit,
    onChange: (JsonElement) -> Unit,
    onReset: () -> Unit,
    t: AppStrings,
) {
    val palette = LocalHbPalette.current
    Column(Modifier.fillMaxWidth()) {
        // Label row
        Row(
            Modifier.fillMaxWidth().padding(bottom = 5.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            HbText(f.label.ifEmpty { f.key }, style = HbType.read.copy(fontSize = 13.sp, fontWeight = FontWeight.Medium), color = palette.text)
            if (f.requiresRestart) {
                // A pill, like every other status chip on the deck.
                Box(
                    Modifier
                        .clip(CircleShape)
                        .background(palette.amber.copy(alpha = 0.10f))
                        .border(1.dp, palette.amber.copy(alpha = 0.28f), CircleShape)
                        .padding(horizontal = 9.dp, vertical = 1.dp),
                ) {
                    HbText(t.settingsConfig.restart, style = HbType.read.copy(fontSize = 12.sp), color = palette.amberBright)
                }
            }
            if (dirty) HbText(t.settingsConfig.editedDot, style = HbType.read.copy(fontSize = 12.5.sp), color = palette.accentBright)
            Spacer(Modifier.weight(1f))
            if (dirty) {
                HbText(
                    t.settingsConfig.revert,
                    style = HbType.readout.copy(fontSize = 10.sp),
                    color = palette.textFaint,
                    modifier = Modifier.clickable(onClick = onReset),
                )
            }
        }

        // Control — typed per field.
        when {
            f.type == "bool" -> {
                val current = if (dirty) edit?.jsonPrimitive?.booleanOrNull ?: false
                else f.value?.jsonPrimitive?.booleanOrNull ?: false
                HbToggle(checked = current, color = palette.accentBright) { onChange(JsonPrimitive(it)) }
            }

            f.type == "select" -> {
                val current = if (dirty) edit?.jsonPrimitive?.contentOrEmpty() ?: ""
                else f.value?.jsonPrimitive?.contentOrEmpty() ?: f.options.firstOrNull().orEmpty()
                InlineSelect(current, f.options) { onChange(JsonPrimitive(it)) }
            }

            f.secret -> {
                val typed = if (dirty) edit?.jsonPrimitive?.contentOrEmpty() ?: "" else ""
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(1f)) {
                        GlassField(
                            value = typed,
                            onValueChange = { onChange(JsonPrimitive(it)) },
                            placeholder = if (f.isSet) t.settingsConfig.storedTypeReplace(f.hint ?: "••••") else (f.placeholder.ifEmpty { t.settingsConfig.notSet }),
                            singleLine = true,
                            dirty = dirty,
                            mono = true,
                            visualTransformation = if (revealed) VisualTransformation.None else PasswordVisualTransformation(),
                        )
                    }
                    SettingsButton(if (revealed) t.common.hide else t.common.show, onClick = onReveal)
                }
            }

            else -> {
                val current = if (dirty) edit?.jsonPrimitive?.contentOrEmpty() ?: ""
                else f.value?.jsonPrimitive?.contentOrEmpty() ?: ""
                GlassField(
                    value = current,
                    onValueChange = { s ->
                        // int fields serialize as JSON numbers (like the web), not strings.
                        if (f.type == "int" || f.type == "float") {
                            val numeric = s.toDoubleOrNull()
                            onChange(if (s.isEmpty() && f.nullable) JsonNull else if (numeric == null) JsonPrimitive(s)
                                else if (f.type == "int" && numeric % 1.0 == 0.0) JsonPrimitive(numeric.toLong()) else JsonPrimitive(numeric))
                        } else {
                            onChange(JsonPrimitive(s))
                        }
                    },
                    placeholder = f.placeholder,
                    singleLine = true,
                    dirty = dirty,
                )
            }
        }

        if (f.help.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Hint(f.help)
        }
        // Clear a stored secret (send empty string → backend clears it).
        if (f.secret && f.isSet && !dirty) {
            HbText(
                t.settingsConfig.clearStored,
                style = HbType.readout.copy(fontSize = 10.sp),
                color = palette.red,
                modifier = Modifier.padding(top = 4.dp).clickable { onChange(JsonPrimitive("")) },
            )
        }
    }
}

/** Inline expand-down select on the glass material (no Material dropdown). */
@Composable
private fun InlineSelect(value: String, options: List<String>, onChange: (String) -> Unit) {
    val palette = LocalHbPalette.current
    var expanded by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().hbGlass(shape = HbGlassShape.Tile, state = HbGlassState.Default)
                .clickable { expanded = !expanded }.padding(horizontal = 12.dp, vertical = 11.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            HbText(value.ifEmpty { "—" }, style = HbType.read.copy(fontSize = 14.sp), color = palette.text, modifier = Modifier.weight(1f))
            val angle by animateFloatAsState(if (expanded) 180f else 0f, label = "sel")
            HbGlyphs.ChevronDown(palette.iconDim, modifier = Modifier.rotate(angle))
        }
        if (expanded) {
            Column(Modifier.fillMaxWidth().hbGlass(shape = HbGlassShape.Tile, state = HbGlassState.Menu)) {
                options.forEach { opt ->
                    val active = opt == value
                    Row(
                        Modifier.fillMaxWidth()
                            .background(if (active) palette.accent.copy(alpha = 0.12f) else Color.Transparent)
                            .clickable { onChange(opt); expanded = false }
                            .padding(horizontal = 12.dp, vertical = 9.dp),
                    ) {
                        HbText(opt, style = HbType.read.copy(fontSize = 13.sp), color = if (active) Color.White else palette.textDim)
                    }
                }
            }
        }
    }
}

/** Pinned save bar reporting unsaved count / applied-live / restart-required. */
@Composable
private fun SaveBar(
    dirtyCount: Int,
    saving: Boolean,
    result: ConfigSaveResult?,
    onDiscard: () -> Unit,
    onSave: () -> Unit,
    t: AppStrings,
) {
    val palette = LocalHbPalette.current
    Row(
        Modifier.fillMaxWidth().hbGlass(shape = HbGlassShape.CardTop).padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Box(Modifier.weight(1f)) {
            if (result != null) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (result.appliedLive.isNotEmpty()) {
                        HbText(t.settingsConfig.appliedLive(result.appliedLive.size), style = HbType.readout.copy(fontSize = 11.sp), color = palette.green)
                    }
                    if (result.restartRequired.isNotEmpty()) {
                        HbText(t.settingsConfig.needsRestartN(result.restartRequired.size), style = HbType.readout.copy(fontSize = 11.sp), color = palette.amber)
                    }
                    if (result.rejected.isNotEmpty()) {
                        HbText(t.settingsConfig.rejected(result.rejected.size), style = HbType.readout.copy(fontSize = 11.sp), color = palette.red)
                    }
                }
            } else {
                HbText(
                    if (dirtyCount > 0) t.settingsConfig.unsavedChanges(dirtyCount) else t.settingsConfig.noChanges,
                    style = HbType.readout.copy(fontSize = 11.sp),
                    color = palette.textFaint,
                )
            }
        }
        if (dirtyCount > 0) SettingsButton(t.settingsConfig.discard, onClick = onDiscard, enabled = !saving)
        SettingsButton(
            if (saving) t.settingsConfig.saving else t.settingsConfig.saveChanges,
            onClick = onSave,
            enabled = dirtyCount > 0 && !saving,
        )
    }
}

/**
 * SourceOfTruthPanel — per-agent source-of-truth memory file. Each agent's
 * chosen memory markdown file is preloaded into its prompt and is where it
 * writes its domain data. The owner picks an existing file per agent.
 */
@Composable
private fun SourceOfTruthPanel(config: AppConfig, graph: AppGraph) {
    val scope = rememberCoroutineScope()
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    var data by remember { mutableStateOf<MemorySources?>(null) }
    var busy by remember { mutableStateOf<String?>(null) }

    suspend fun load() { data = graph.api.getMemorySources(config) }
    LaunchedEffect(config) { load() }

    val d = data ?: return
    if (d.agents.isEmpty()) return
    fun fileName(p: String) = p.removePrefix("/memories/")

    Column(Modifier.fillMaxWidth().hbGlass(shape = HbGlassShape.Ctl).padding(12.dp)) {
        HbText(if (d.assignable) t.settingsConfig.sourceOfTruthTitle else "Iacon Catalogue", style = HbType.read.copy(fontSize = 14.sp, fontWeight = FontWeight.Bold), color = palette.text)
        Hint(if (d.assignable) t.settingsConfig.sourceOfTruthHint else "All agents use the shared Iacon catalogue. Orion maintains evidence-backed, versioned records.")
        Spacer(Modifier.height(8.dp))
        d.agents.forEach { a ->
            val options = buildList {
                add("") // none / default
                addAll(d.files)
            }
            val current = if (a.source != null && d.files.contains(a.source)) a.source!! else ""
            Row(
                Modifier.fillMaxWidth().padding(vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Column(Modifier.width(110.dp)) {
                    HbText(a.name.ifEmpty { a.agentId }, style = HbType.read.copy(fontSize = 13.sp, fontWeight = FontWeight.Medium), color = palette.text, maxLines = 1)
                    HbText(if (d.assignable) a.source?.let { fileName(it) } ?: "—" else "Iacon", style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint, maxLines = 1)
                }
                Box(Modifier.weight(1f)) {
                    if (!d.assignable) {
                        Hint("Shared governed memory")
                    } else InlineSelect(
                        value = if (current.isEmpty()) (a.default?.let { t.settingsConfig.default(fileName(it)) } ?: t.settingsConfig.none) else fileName(current),
                        options = options.map { if (it.isEmpty()) (a.default?.let { d0 -> t.settingsConfig.default(fileName(d0)) } ?: t.settingsConfig.none) else fileName(it) },
                    ) { picked ->
                        // Map the display label back to the wire path.
                        val path = d.files.firstOrNull { fileName(it) == picked }
                        scope.launch {
                            busy = a.agentId
                            graph.api.setMemorySource(config, a.agentId, path)
                            load()
                            busy = null
                        }
                    }
                }
            }
        }
    }
}

private fun JsonPrimitive.contentOrEmpty(): String = if (this is kotlinx.serialization.json.JsonNull) "" else content
