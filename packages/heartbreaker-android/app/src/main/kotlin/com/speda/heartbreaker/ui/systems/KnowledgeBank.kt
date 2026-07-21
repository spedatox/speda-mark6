package com.speda.heartbreaker.ui.systems

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
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
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.MemoryCommitResult
import com.speda.heartbreaker.data.MemoryFileInfo
import com.speda.heartbreaker.data.MemoryRevisionInfo
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbText
import com.speda.heartbreaker.ui.prose.ProseText
import com.speda.heartbreaker.ui.settings.GlassField
import com.speda.heartbreaker.ui.settings.Panel
import com.speda.heartbreaker.ui.settings.SettingsButton
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * DATA_BANKS // KNOWLEDGE — mobile port of SystemsBoard.tsx's knowledge bank:
 * what SPEDA knows about the owner, browsable and (for canonical files) owner
 * editable. Desktop's file rail + fact readout become a horizontal chip row
 * over a single content pane (mobile spec: one scrollable column, no side
 * rail). EDIT/COMMIT with the 409-conflict reload, and HISTORY/RESTORE, are
 * unchanged behaviour.
 */
@Composable
fun KnowledgeBank(config: AppConfig, api: IgorApi) {
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()

    var files by remember { mutableStateOf<List<MemoryFileInfo>>(emptyList()) }
    var selectedPath by remember { mutableStateOf<String?>(null) }
    var editing by remember { mutableStateOf(false) }
    var draft by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var saveMsg by remember { mutableStateOf<String?>(null) }
    var revs by remember { mutableStateOf<List<MemoryRevisionInfo>?>(null) }

    LaunchedEffect(config) {
        val loaded = api.fetchMemoryFiles(config)
        files = loaded
        selectedPath = (loaded.firstOrNull { it.path.endsWith("/owner.md") } ?: loaded.firstOrNull())?.path
    }

    // Switching files always drops out of edit / history mode.
    LaunchedEffect(selectedPath) { editing = false; revs = null; saveMsg = null }

    val selFile = files.firstOrNull { it.path == selectedPath }

    fun applyFresh(f: MemoryFileInfo) {
        files = files.map { if (it.path == f.path) f else it }
    }

    fun commit() {
        val file = selFile ?: return
        scope.launch {
            saving = true; saveMsg = null
            when (val res = api.commitMemoryFile(config, file.path, draft, file.updatedAt)) {
                is MemoryCommitResult.Ok -> {
                    applyFresh(res.file)
                    editing = false
                    saveMsg = "Committed."
                }
                is MemoryCommitResult.Conflict -> {
                    res.current?.let { applyFresh(it); draft = it.content }
                    saveMsg = "This file changed since you opened it — reloaded the latest. Re-apply your edit."
                }
                MemoryCommitResult.Failed -> saveMsg = "Commit failed."
            }
            saving = false
        }
    }

    fun openHistory() {
        val file = selFile ?: return
        scope.launch { revs = api.fetchMemoryRevisions(config, file.path) }
    }

    fun restore(id: Int) {
        scope.launch {
            val f = api.restoreMemoryRevision(config, id)
            if (f != null) {
                applyFresh(f)
                revs = null; editing = false; saveMsg = "Restored."
            } else {
                saveMsg = "Restore failed."
            }
        }
    }

    Panel {
        if (files.isEmpty()) {
            HbText("NO RECORDS", style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
            return@Panel
        }

        HbText("${files.size} FILES", style = HbType.readout.copy(fontSize = 9.sp), color = palette.icon, modifier = Modifier.padding(bottom = 6.dp))

        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            files.forEach { f ->
                val name = f.path.substringAfterLast('/').removeSuffix(".md").uppercase(Locale.ENGLISH)
                FileChip(name, selected = f.path == selectedPath) { selectedPath = f.path }
            }
        }

        val file = selFile ?: return@Panel
        Spacer(Modifier.height(10.dp))

        // ── Toolbar ──────────────────────────────────────────────────────────
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            saveMsg?.let { HbText(it, style = HbType.readout.copy(fontSize = 9.5.sp), color = palette.amber, modifier = Modifier.weight(1f)) }
                ?: run {
                    if (file.updatedAt != null && !editing && revs == null) {
                        HbText("LAST WRITE ${fmtDate(file.updatedAt)}", style = HbType.readout.copy(fontSize = 9.sp), color = palette.textFaint, modifier = Modifier.weight(1f))
                    } else {
                        Spacer(Modifier.weight(1f))
                    }
                }
            when {
                editing -> {
                    SettingsButton("Cancel", onClick = { editing = false }, enabled = !saving, tint = palette.textDim)
                    SettingsButton(if (saving) "Saving…" else "Commit", onClick = ::commit, enabled = !saving, tint = palette.amberBright)
                }
                revs != null -> SettingsButton("Close", onClick = { revs = null })
                file.editable -> {
                    SettingsButton("History", onClick = ::openHistory, tint = palette.textDim)
                    SettingsButton("Edit", onClick = { draft = file.content; saveMsg = null; editing = true })
                }
            }
        }

        Spacer(Modifier.height(8.dp))

        when {
            editing -> GlassField(
                value = draft,
                onValueChange = { draft = it },
                placeholder = "",
                singleLine = false,
                minHeight = 220.dp,
                mono = true,
            )
            revs != null -> RevisionList(revs.orEmpty(), onRestore = ::restore)
            file.content.isBlank() -> HbText(
                "// EMPTY — SPEDA HAS NOT WRITTEN HERE YET",
                style = HbType.readout.copy(fontSize = 10.sp),
                color = palette.textFaint,
            )
            else -> ProseText(file.content, modifier = Modifier.fillMaxWidth())
        }
    }
}

@Composable
private fun FileChip(label: String, selected: Boolean, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .hbGlass(shape = HbGlassShape.Pill, state = if (selected) HbGlassState.Tint(palette.amber) else HbGlassState.Default)
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        HbText("▸", style = HbType.readout.copy(fontSize = 9.sp), color = if (selected) palette.amber else palette.iconDim)
        HbText(label, style = HbType.readout.copy(fontSize = 10.sp), color = if (selected) palette.amberBright else palette.icon, maxLines = 1)
    }
}

@Composable
private fun RevisionList(revs: List<MemoryRevisionInfo>, onRestore: (Int) -> Unit) {
    val palette = LocalHbPalette.current
    if (revs.isEmpty()) {
        HbText("// NO REVISIONS YET", style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
        return
    }
    Column {
        revs.forEach { r ->
            Row(
                Modifier.fillMaxWidth().padding(vertical = 5.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                HbText(r.createdAt?.let { fmtDate(it) } ?: "--", style = HbType.readout.copy(fontSize = 9.5.sp), color = palette.textFaint)
                HbText(r.author, style = HbType.readout.copy(fontSize = 9.5.sp), color = if (r.author == "owner") palette.amber else palette.accent)
                HbText(r.action, style = HbType.readout.copy(fontSize = 9.5.sp), color = palette.textDim, modifier = Modifier.weight(1f))
                SettingsButton("Restore", onClick = { onRestore(r.id) }, tint = palette.textDim)
            }
        }
    }
}

private val DATE_FMT: DateTimeFormatter = DateTimeFormatter.ofPattern("dd.MM.yy", Locale.ENGLISH)

private fun fmtDate(iso: String): String {
    val withZone = if (iso.endsWith("Z") || iso.contains("+")) iso else "${iso}Z"
    val instant = runCatching { Instant.parse(withZone) }.getOrElse { return "--" }
    return DATE_FMT.format(instant.atZone(ZoneId.systemDefault()))
}
