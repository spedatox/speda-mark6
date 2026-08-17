package com.speda.heartbreaker.ui.systems

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.MemoryCommitResult
import com.speda.heartbreaker.data.MemoryFileInfo
import com.speda.heartbreaker.data.MemoryRevisionInfo
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.glass.toShape
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.MemoryTree
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
 * DATA_BANKS // KNOWLEDGE — what SPEDA knows about the owner, browsable and
 * (for canonical files) owner-editable.
 *
 * A FILE EXPLORER, not a document. Two things forced this. Memory became a
 * TREE, and a flat strip of leaf names stopped saying anything the moment it
 * did: `sessions.md`, `ledger.md`, `notes.md` and `profile.md` read alike, and
 * eighty of them in one horizontal scroller told the owner nothing about which
 * domain he was looking at. And rendering the selected file's prose inline made
 * the systems board scroll for screens — the board is a glanceable readout, and
 * one document was swallowing it.
 *
 * So the panel shows only the tree, and a file's contents open in their own
 * window. EDIT/COMMIT with the 409-conflict reload, and HISTORY/RESTORE, moved
 * into that window unchanged.
 */
@Composable
fun KnowledgeBank(config: AppConfig, api: IgorApi) {
    val palette = LocalHbPalette.current

    var files by remember { mutableStateOf<List<MemoryFileInfo>>(emptyList()) }
    var openPath by remember { mutableStateOf<String?>(null) }
    /** Folders start OPEN: a collapsed tree hides the thing the panel exists
     *  to show, and the owner opened it to look at his memory. */
    var folded by remember { mutableStateOf<Set<String>>(emptySet()) }

    LaunchedEffect(config) { files = api.fetchMemoryFiles(config) }

    val tree = remember(files) { MemoryTree.group(files) { it.path } }

    Panel {
        if (files.isEmpty()) {
            HbText("No records", style = HbType.read.copy(fontSize = 14.sp), color = palette.textFaint)
            return@Panel
        }

        HbText(
            "${files.size} files · ${tree.size} folders",
            style = HbType.read.copy(fontSize = 13.sp),
            color = palette.textFaint,
            modifier = Modifier.padding(bottom = 8.dp),
        )

        tree.forEach { (dir, group) ->
            val open = dir !in folded
            if (dir.isNotEmpty()) {
                FolderRow(dir, group.size, open) {
                    folded = if (open) folded + dir else folded - dir
                }
            }
            if (open) {
                group.forEach { f ->
                    FileRow(
                        // Inside a folder the folder name is already overhead,
                        // so the row shows the leaf alone.
                        label = f.path.substringAfterLast('/').removeSuffix(".md"),
                        indented = dir.isNotEmpty(),
                        editable = f.editable,
                        onClick = { openPath = f.path },
                    )
                }
            }
        }
    }

    val file = files.firstOrNull { it.path == openPath }
    if (file != null) {
        MemoryFileWindow(
            config = config,
            api = api,
            file = file,
            onClose = { openPath = null },
            onFileChanged = { fresh -> files = files.map { if (it.path == fresh.path) fresh else it } },
        )
    }
}

/** A folder in the tree — its own row, with the count it is hiding when shut. */
@Composable
private fun FolderRow(dir: String, count: Int, open: Boolean, onToggle: () -> Unit) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onToggle)
            .padding(top = 10.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (open) HbGlyphs.ChevronDown(palette.textFaint, size = 11.dp)
        else HbGlyphs.ChevronUp(palette.textFaint, size = 11.dp)
        // A folder name IS a section label — the deck keeps those in Condensed
        // caps (see the telemetry column), so this is not the old FUI voice.
        HbText(
            dir,
            style = HbType.headerBar.copy(fontSize = 12.sp),
            color = palette.textDim,
            caps = true,
            modifier = Modifier.weight(1f),
        )
        if (!open) {
            HbText("$count", style = HbType.read.copy(fontSize = 12.sp), color = palette.textFaint)
        }
    }
}

/** A file in the tree. Tapping it opens the window — nothing renders in place. */
@Composable
private fun FileRow(label: String, indented: Boolean, editable: Boolean, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .padding(start = if (indented) 14.dp else 0.dp, top = 2.dp, bottom = 2.dp)
            .clip(HbGlassShape.Tile.toShape())
            .background(Color.White.copy(alpha = 0.03f))
            .border(1.dp, Color.White.copy(alpha = 0.06f), HbGlassShape.Tile.toShape())
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        HbGlyphs.File(palette.accent, size = 13.dp)
        HbText(
            label,
            style = HbType.read.copy(fontSize = 14.sp),
            color = palette.text,
            maxLines = 1,
            modifier = Modifier.weight(1f),
        )
        // Only the canonical files can be written by hand; saying so here saves
        // opening one to find out.
        if (editable) {
            HbText("editable", style = HbType.read.copy(fontSize = 11.5.sp), color = palette.textFaint)
        }
        HbGlyphs.ChevronRight(palette.textFaint, size = 12.dp)
    }
}

/**
 * One memory file, full-screen. This is where reading, editing and history all
 * happen — the board behind it stays a tree.
 */
@Composable
private fun MemoryFileWindow(
    config: AppConfig,
    api: IgorApi,
    file: MemoryFileInfo,
    onClose: () -> Unit,
    onFileChanged: (MemoryFileInfo) -> Unit,
) {
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()

    var editing by remember(file.path) { mutableStateOf(false) }
    var draft by remember(file.path) { mutableStateOf("") }
    var saving by remember(file.path) { mutableStateOf(false) }
    var saveMsg by remember(file.path) { mutableStateOf<String?>(null) }
    var revs by remember(file.path) { mutableStateOf<List<MemoryRevisionInfo>?>(null) }

    fun commit() {
        saving = true
        saveMsg = null
        scope.launch {
            when (val res = api.commitMemoryFile(config, file.path, draft, file.updatedAt)) {
                is MemoryCommitResult.Ok -> {
                    onFileChanged(res.file); editing = false; saveMsg = "Saved."
                }
                is MemoryCommitResult.Conflict -> {
                    saveMsg = "Changed on the server — reloaded."
                    res.current?.let { onFileChanged(it); draft = it.content }
                }
                else -> saveMsg = "Save failed."
            }
            saving = false
        }
    }

    fun restore(id: Int) {
        scope.launch {
            // The revision id identifies the file too — the endpoint takes it
            // alone, no path.
            val fresh = api.restoreMemoryRevision(config, id)
            if (fresh != null) { onFileChanged(fresh); revs = null; saveMsg = "Restored." }
            else saveMsg = "Restore failed."
        }
    }

    // Back closes whatever is on top: history, then editing, then the window.
    BackHandler {
        when {
            revs != null -> revs = null
            editing -> editing = false
            else -> onClose()
        }
    }

    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(
            Modifier
                .fillMaxWidth()
                .fillMaxHeight(0.92f)
                .padding(10.dp)
                .hbGlass(shape = HbGlassShape.Island, state = HbGlassState.Menu)
                .padding(16.dp),
        ) {
            // ── Title bar ────────────────────────────────────────────────────
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Column(Modifier.weight(1f)) {
                    HbText(
                        file.path.substringAfterLast('/').removeSuffix(".md"),
                        style = HbType.headerBar.copy(fontSize = 18.sp),
                        color = palette.text,
                        maxLines = 1,
                    )
                    // The full path, because the leaf alone is what stopped
                    // identifying a file once memory became a tree.
                    HbText(
                        file.path.removePrefix("/memories/"),
                        style = HbType.read.copy(fontSize = 12.sp),
                        color = palette.textFaint,
                        maxLines = 1,
                    )
                }
                Box(
                    Modifier.size(34.dp).hbGlass(shape = HbGlassShape.Tile).clickable(onClick = onClose),
                    contentAlignment = Alignment.Center,
                ) { HbGlyphs.Close(palette.textDim, size = 14.dp) }
            }

            Spacer(Modifier.height(12.dp))

            // ── Toolbar ──────────────────────────────────────────────────────
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                val note = saveMsg
                    ?: file.updatedAt?.takeIf { !editing && revs == null }?.let { "Last write ${fmtDate(it)}" }
                if (note != null) {
                    HbText(
                        note,
                        style = HbType.read.copy(fontSize = 12.5.sp),
                        color = if (saveMsg != null) palette.amberBright else palette.textFaint,
                        modifier = Modifier.weight(1f),
                    )
                } else {
                    Spacer(Modifier.weight(1f))
                }
                when {
                    editing -> {
                        SettingsButton("Cancel", onClick = { editing = false }, enabled = !saving)
                        SettingsButton(
                            if (saving) "Saving…" else "Commit",
                            onClick = ::commit,
                            enabled = !saving,
                            tint = palette.amberBright,
                        )
                    }
                    revs != null -> SettingsButton("Close history", onClick = { revs = null })
                    file.editable -> {
                        SettingsButton("History", onClick = {
                            scope.launch { revs = api.fetchMemoryRevisions(config, file.path) }
                        })
                        SettingsButton(
                            "Edit",
                            onClick = { draft = file.content; saveMsg = null; editing = true },
                            tint = palette.accent,
                        )
                    }
                }
            }

            Spacer(Modifier.height(12.dp))

            // ── Body ─────────────────────────────────────────────────────────
            Column(Modifier.fillMaxWidth().weight(1f).verticalScroll(rememberScrollState())) {
                when {
                    editing -> GlassField(
                        value = draft,
                        onValueChange = { draft = it },
                        placeholder = "",
                        singleLine = false,
                        minHeight = 300.dp,
                        mono = true,
                    )
                    revs != null -> RevisionList(revs.orEmpty(), onRestore = ::restore)
                    file.content.isBlank() -> HbText(
                        "Empty — SPEDA has not written here yet.",
                        style = HbType.read.copy(fontSize = 14.sp),
                        color = palette.textFaint,
                    )
                    else -> ProseText(file.content, modifier = Modifier.fillMaxWidth())
                }
            }
        }
    }
}

@Composable
private fun RevisionList(revs: List<MemoryRevisionInfo>, onRestore: (Int) -> Unit) {
    val palette = LocalHbPalette.current
    if (revs.isEmpty()) {
        HbText("No revisions yet.", style = HbType.read.copy(fontSize = 14.sp), color = palette.textFaint)
        return
    }
    Column {
        revs.forEach { r ->
            Row(
                Modifier.fillMaxWidth().padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                HbText(
                    r.createdAt?.let { fmtDate(it) } ?: "—",
                    style = HbType.read.copy(fontSize = 12.5.sp),
                    color = palette.textFaint,
                )
                HbText(
                    r.author,
                    style = HbType.read.copy(fontSize = 12.5.sp),
                    color = if (r.author == "owner") palette.amberBright else palette.accent,
                )
                HbText(
                    r.action,
                    style = HbType.read.copy(fontSize = 12.5.sp),
                    color = palette.textDim,
                    modifier = Modifier.weight(1f),
                )
                SettingsButton("Restore", onClick = { onRestore(r.id) })
            }
        }
    }
}

private val DATE_FMT: DateTimeFormatter = DateTimeFormatter.ofPattern("dd.MM.yy", Locale.ENGLISH)

private fun fmtDate(iso: String): String {
    val withZone = if (iso.endsWith("Z") || iso.contains("+")) iso else "${iso}Z"
    val instant = runCatching { Instant.parse(withZone) }.getOrElse { return "—" }
    return DATE_FMT.format(instant.atZone(ZoneId.systemDefault()))
}
