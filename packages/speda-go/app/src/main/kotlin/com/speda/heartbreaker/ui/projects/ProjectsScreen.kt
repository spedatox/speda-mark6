// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.projects

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.data.Attachments
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.Project
import com.speda.heartbreaker.domain.ProjectFile
import com.speda.heartbreaker.domain.Session
import com.speda.heartbreaker.i18n.AppStrings
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.launch

/**
 * Projects — the workspace surface, ported from the desktop's ProjectsView.tsx.
 *
 * A project owns three things a loose chat has none of: its own chat list, its
 * own standing instructions, and its own knowledge base. This screen is both
 * halves of that — the list of projects, and the detail pane for one — stacked
 * rather than side by side, because the phone has one column.
 *
 * Isolation is inherited, not implemented here: every call goes through
 * [IgorApi]'s project functions, which stamp `config.agentId` on the request,
 * and the backend refuses a cross-agent read. Switching agents switches the
 * whole project set exactly the way it already switches chat history.
 */
@Composable
fun ProjectsScreen(
    config: AppConfig,
    api: IgorApi,
    /** Open straight into this project, when the caller came from a shelf row. */
    initialProjectId: Int?,
    onClose: () -> Unit,
    onOpenChat: (sessionId: Int, projectId: Int) -> Unit,
    onNewChat: (projectId: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val t = LocalStrings.current
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()

    var projects by remember { mutableStateOf<List<Project>?>(null) }
    // Keyed on the argument: opening a different project from the shelf while
    // this screen is already composed has to move the view, not be swallowed by
    // a remember that only ever read its first value.
    var openId by remember(initialProjectId) { mutableStateOf(initialProjectId) }
    var showArchived by remember { mutableStateOf(false) }
    var creating by remember { mutableStateOf(false) }

    suspend fun reload() { projects = api.fetchProjects(config, showArchived) }
    LaunchedEffect(config, showArchived) { reload() }
    // An agent switch rewrites config.agentId, which reloads the list above. The
    // OPEN project has to close with it: it belongs to the agent we just left,
    // and the backend would (correctly) refuse every call it makes.
    //
    // Guarded on an actual CHANGE, not on the effect firing: a bare
    // LaunchedEffect(config.agentId) also runs on first composition, which would
    // null out the initialProjectId we were opened with and drop the owner on
    // the grid every time they tapped a project on the sidebar shelf.
    var lastAgentId by remember { mutableStateOf(config.agentId) }
    LaunchedEffect(config.agentId) {
        if (config.agentId != lastAgentId) {
            lastAgentId = config.agentId
            openId = null
        }
    }

    val open = projects?.firstOrNull { it.id == openId }

    Column(modifier.fillMaxSize().navigationBarsPadding()) {
        if (open != null) {
            ProjectDetail(
                config = config,
                api = api,
                project = open,
                onBack = { openId = null },
                onChanged = { updated ->
                    // null = deleted: drop the row. Otherwise swap it in place, so
                    // an edit shows on the card without a round trip.
                    projects = if (updated == null) {
                        projects?.filterNot { it.id == open.id }
                    } else {
                        projects?.map { if (it.id == open.id) updated else it }
                    }
                    if (updated == null) openId = null
                },
                onOpenChat = { sessionId -> onOpenChat(sessionId, open.id) },
                onNewChat = { onNewChat(open.id) },
                t = t,
            )
        } else {
            // ── Header ───────────────────────────────────────────────────────
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                HbText(
                    t.projects.title,
                    style = HbType.read.copy(fontSize = 20.sp, fontWeight = FontWeight.SemiBold),
                    color = palette.text,
                )
                Spacer(Modifier.weight(1f))
                PillButton(t.projects.showArchived, if (showArchived) palette.amber else palette.iconDim) {
                    showArchived = !showArchived
                }
                Spacer(Modifier.width(6.dp))
                PillButton(t.projects.newProject, palette.accentBright) { creating = !creating }
                Spacer(Modifier.width(4.dp))
                Box(Modifier.size(28.dp).clickable(onClick = onClose), Alignment.Center) {
                    HbGlyphs.Close(palette.iconDim, 13.dp)
                }
            }

            if (creating) {
                NewProjectForm(
                    t = t,
                    onCancel = { creating = false },
                    onCreate = { name, description, instructions, icon ->
                        scope.launch {
                            val made = api.createProject(config, name, description, instructions, icon)
                            creating = false
                            if (made != null) {
                                projects = listOf(made) + (projects ?: emptyList())
                                openId = made.id
                            }
                        }
                    },
                )
            }

            // ── The grid, as a single column of cards ────────────────────────
            val list = projects
            when {
                list == null -> Unit  // first load — the screen stays empty briefly
                list.isEmpty() -> EmptyState(t.projects.empty, t.projects.emptyBlurb)
                else -> LazyColumn(
                    Modifier.fillMaxSize().padding(horizontal = 14.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(list, key = { it.id }) { project ->
                        ProjectCard(
                            project = project,
                            t = t,
                            onOpen = { openId = project.id },
                            onPin = {
                                scope.launch {
                                    api.updateProject(config, project.id, pinned = !project.pinned)
                                    reload()
                                }
                            },
                            onArchive = {
                                scope.launch {
                                    api.updateProject(config, project.id, archived = !project.archived)
                                    reload()
                                }
                            },
                        )
                    }
                    item { Spacer(Modifier.height(18.dp)) }
                }
            }
        }
    }
}

/* ── Project card ─────────────────────────────────────────────────────────── */
@Composable
private fun ProjectCard(
    project: Project,
    t: AppStrings,
    onOpen: () -> Unit,
    onPin: () -> Unit,
    onArchive: () -> Unit,
) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .hbGlass(shape = HbGlassShape.Card, state = HbGlassState.Default)
            .clickable(onClick = onOpen)
            .padding(horizontal = 12.dp, vertical = 11.dp),
    ) {
        // The accent tab — a project's one piece of colour, so a familiar list is
        // scannable by hue before it is readable by name.
        Box(
            Modifier
                .width(2.dp)
                .heightIn(min = 34.dp)
                .background(palette.accent.copy(alpha = if (project.pinned) 0.95f else 0.4f)),
        )
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (project.icon.isNotBlank()) {
                    HbText(project.icon, style = HbType.read.copy(fontSize = 15.sp), color = palette.text)
                } else {
                    HbGlyphs.Folder(palette.accent, 14.dp)
                }
                Spacer(Modifier.width(7.dp))
                HbText(
                    project.name,
                    style = HbType.read.copy(fontSize = 15.sp, fontWeight = FontWeight.SemiBold),
                    color = palette.text,
                    maxLines = 1,
                    modifier = Modifier.weight(1f),
                )
            }
            if (project.description.isNotBlank()) {
                Spacer(Modifier.height(5.dp))
                HbText(
                    project.description,
                    style = HbType.read.copy(fontSize = 12.5.sp),
                    color = palette.textDim,
                    maxLines = 3,
                )
            }
            Spacer(Modifier.height(9.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                HbGlyphs.Chat(palette.textFaint, 10.dp)
                Spacer(Modifier.width(4.dp))
                HbText("${project.chatCount}", style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
                Spacer(Modifier.width(10.dp))
                HbGlyphs.File(palette.textFaint, 10.dp)
                Spacer(Modifier.width(4.dp))
                HbText("${project.fileCount}", style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
                if (project.archived) {
                    Spacer(Modifier.width(10.dp))
                    HbText(
                        t.projects.archived,
                        style = HbType.readout.copy(fontSize = 10.sp),
                        color = palette.amber,
                    )
                }
            }
        }
        Column(horizontalAlignment = Alignment.End) {
            Box(Modifier.size(26.dp).clickable(onClick = onPin), Alignment.Center) {
                HbGlyphs.Pin(if (project.pinned) palette.accentBright else palette.iconDim, 12.dp)
            }
            Box(Modifier.size(26.dp).clickable(onClick = onArchive), Alignment.Center) {
                HbGlyphs.Archive(palette.iconDim, 12.dp)
            }
        }
    }
}

/* ── New-project composer ─────────────────────────────────────────────────── */
@Composable
private fun NewProjectForm(
    t: AppStrings,
    onCancel: () -> Unit,
    onCreate: (name: String, description: String, instructions: String, icon: String) -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }
    var instructions by remember { mutableStateOf("") }
    var icon by remember { mutableStateOf("") }
    val palette = LocalHbPalette.current

    Column(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp)
            .hbGlass(shape = HbGlassShape.Card, state = HbGlassState.Default)
            .padding(12.dp),
    ) {
        Row {
            Field(icon, { icon = it.take(2) }, "🗂", Modifier.width(52.dp))
            Spacer(Modifier.width(8.dp))
            Field(name, { name = it }, t.projects.namePlaceholder, Modifier.weight(1f))
        }
        Spacer(Modifier.height(8.dp))
        Field(description, { description = it }, t.projects.descriptionPlaceholder, Modifier.fillMaxWidth())
        Spacer(Modifier.height(8.dp))
        Field(
            instructions, { instructions = it }, t.projects.instructionsPlaceholder,
            Modifier.fillMaxWidth().heightIn(min = 68.dp), singleLine = false,
        )
        Spacer(Modifier.height(10.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            PillButton(t.projects.cancel, palette.iconDim, onCancel)
            Spacer(Modifier.width(6.dp))
            PillButton(t.projects.create, palette.accentBright) {
                if (name.isNotBlank()) onCreate(name, description, instructions, icon)
            }
        }
    }
    Spacer(Modifier.height(10.dp))
}

/* ── Detail pane ──────────────────────────────────────────────────────────── */
@Composable
private fun ProjectDetail(
    config: AppConfig,
    api: IgorApi,
    project: Project,
    onBack: () -> Unit,
    /** null = the project was deleted. */
    onChanged: (Project?) -> Unit,
    onOpenChat: (Int) -> Unit,
    onNewChat: () -> Unit,
    t: AppStrings,
) {
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()

    var name by remember(project.id) { mutableStateOf(project.name) }
    var description by remember(project.id) { mutableStateOf(project.description) }
    var instructions by remember(project.id) { mutableStateOf(project.instructions) }
    var sessions by remember(project.id) { mutableStateOf<List<Session>?>(null) }
    var files by remember(project.id) { mutableStateOf<List<ProjectFile>?>(null) }
    var uploading by remember { mutableStateOf<List<String>>(emptyList()) }
    var uploadError by remember { mutableStateOf("") }
    var confirmDelete by remember { mutableStateOf(false) }

    LaunchedEffect(config, project.id) {
        sessions = api.fetchProjectSessions(config, project.id)
        files = api.fetchProjectFiles(config, project.id)
    }

    val context = LocalContext.current
    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments(),
    ) { uris ->
        if (uris.isEmpty()) return@rememberLauncherForActivityResult
        uploadError = ""
        scope.launch {
            // Sequential, not parallel: each upload runs a server-side extractor,
            // and the per-project file cap is checked per request — firing them at
            // once would race past the limit and report the failures out of order.
            for (uri in uris) {
                val doc = Attachments.docBlock(context, uri) ?: continue
                uploading = uploading + doc.name
                api.uploadProjectFile(config, project.id, doc)
                    .onSuccess { added -> files = listOf(added) + (files ?: emptyList()) }
                    .onFailure { err -> uploadError = err.message ?: "" }
                uploading = uploading - doc.name
            }
        }
    }

    fun save() {
        scope.launch {
            api.updateProject(
                config, project.id,
                name = name, description = description, instructions = instructions,
            )?.let(onChanged)
        }
    }

    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(28.dp).clickable(onClick = onBack), Alignment.Center) {
                    // Back is the forward chevron turned around — one glyph, two
                    // directions, rather than a second path to keep in step.
                    HbGlyphs.ChevronRight(
                        palette.iconDim, 12.dp,
                        Modifier.graphicsLayer { rotationZ = 180f },
                    )
                }
                Spacer(Modifier.width(4.dp))
                HbText(
                    project.name,
                    style = HbType.read.copy(fontSize = 18.sp, fontWeight = FontWeight.SemiBold),
                    color = palette.text,
                    maxLines = 1,
                    modifier = Modifier.weight(1f),
                )
                PillButton(t.projects.newChatHere, palette.accentBright, onNewChat)
            }
        }

        // ── Instructions ─────────────────────────────────────────────────────
        item {
            SectionLabel(t.projects.instructions)
            HbText(
                t.projects.instructionsBlurb,
                style = HbType.read.copy(fontSize = 11.5.sp),
                color = palette.textFaint,
                modifier = Modifier.padding(bottom = 6.dp),
            )
            Field(
                instructions, { instructions = it }, t.projects.instructionsPlaceholder,
                Modifier.fillMaxWidth().heightIn(min = 96.dp), singleLine = false,
                onCommit = { save() },
            )
            Spacer(Modifier.height(6.dp))
            Field(
                description, { description = it }, t.projects.descriptionPlaceholder,
                Modifier.fillMaxWidth(), onCommit = { save() },
            )
        }

        // ── Knowledge ────────────────────────────────────────────────────────
        item {
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                SectionLabel(t.projects.knowledge, Modifier.weight(1f))
                PillButton(t.projects.addFiles, palette.accentBright) { picker.launch(arrayOf("*/*")) }
            }
            HbText(
                t.projects.knowledgeBlurb,
                style = HbType.read.copy(fontSize = 11.5.sp),
                color = palette.textFaint,
                modifier = Modifier.padding(bottom = 6.dp),
            )
            if (uploadError.isNotBlank()) {
                HbText(
                    uploadError,
                    style = HbType.read.copy(fontSize = 11.5.sp),
                    color = palette.red,
                    modifier = Modifier.padding(bottom = 6.dp),
                )
            }
        }
        items(uploading, key = { "up-$it" }) { fileName ->
            FileChip(fileName, "${t.projects.uploading}…", palette.accent, null)
        }
        items(files ?: emptyList(), key = { it.id }) { file ->
            FileChip(file.name, fmtBytes(file.size), palette.textDim) {
                files = (files ?: emptyList()).filterNot { it.id == file.id }
                scope.launch { api.deleteProjectFile(config, project.id, file.id) }
            }
        }
        if (files?.isEmpty() == true && uploading.isEmpty()) {
            item {
                HbText(t.projects.noFiles, style = HbType.read.copy(fontSize = 12.sp), color = palette.textFaint)
            }
        }

        // ── Chats ────────────────────────────────────────────────────────────
        item {
            Spacer(Modifier.height(10.dp))
            SectionLabel(t.projects.chats)
        }
        val chats = sessions
        if (chats != null && chats.isEmpty()) {
            item {
                HbText(t.projects.noChats, style = HbType.read.copy(fontSize = 12.sp), color = palette.textFaint)
            }
        }
        items(chats ?: emptyList(), key = { it.id }) { session ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .clickable { onOpenChat(session.id) }
                    .padding(horizontal = 8.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                HbGlyphs.Chat(palette.accent, 12.dp)
                Spacer(Modifier.width(8.dp))
                HbText(
                    session.title ?: t.sidebar.newConversation,
                    style = HbType.read.copy(fontSize = 13.5.sp),
                    color = palette.textDim,
                    maxLines = 1,
                )
            }
        }

        // ── Delete ───────────────────────────────────────────────────────────
        item {
            Spacer(Modifier.height(18.dp))
            if (!confirmDelete) {
                PillButton(t.projects.delete, palette.red) { confirmDelete = true }
            } else {
                Column {
                    HbText(
                        t.projects.deleteConfirm,
                        style = HbType.read.copy(fontSize = 12.sp),
                        color = palette.textDim,
                        modifier = Modifier.padding(bottom = 6.dp),
                    )
                    Row {
                        PillButton(t.projects.cancel, palette.iconDim) { confirmDelete = false }
                        Spacer(Modifier.width(6.dp))
                        PillButton(t.projects.delete, palette.red) {
                            scope.launch {
                                api.deleteProject(config, project.id)
                                onChanged(null)
                                onBack()
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

/* ── Small shared pieces ──────────────────────────────────────────────────── */

@Composable
private fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    HbText(
        text,
        style = HbType.headerBar.copy(fontSize = 11.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.16.em),
        color = LocalHbPalette.current.iconDim,
        caps = true,
        modifier = modifier.padding(bottom = 4.dp),
    )
}

@Composable
private fun PillButton(label: String, tint: Color, onClick: () -> Unit) {
    Box(
        Modifier
            .hbGlass(shape = HbGlassShape.Tile, state = HbGlassState.Default)
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 6.dp),
    ) {
        HbText(
            label,
            style = HbType.headerBar.copy(fontSize = 10.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.12.em),
            color = tint,
            caps = true,
            maxLines = 1,
        )
    }
}

@Composable
private fun Field(
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    modifier: Modifier = Modifier,
    singleLine: Boolean = true,
    onCommit: (() -> Unit)? = null,
) {
    val palette = LocalHbPalette.current
    Box(
        modifier
            .hbGlass(shape = HbGlassShape.Tile, state = HbGlassState.Default)
            .padding(horizontal = 9.dp, vertical = 8.dp),
    ) {
        if (value.isEmpty()) {
            HbText(placeholder, style = HbType.read.copy(fontSize = 13.sp), color = palette.textFaint, maxLines = 2)
        }
        BasicTextField(
            value = value,
            onValueChange = onValueChange,
            singleLine = singleLine,
            textStyle = HbType.read.copy(fontSize = 13.sp).merge(TextStyle(color = palette.text)),
            cursorBrush = SolidColor(palette.accentBright),
            modifier = Modifier
                .fillMaxWidth()
                // Commit on blur rather than per keystroke: this writes to the
                // backend, and a PATCH per character is not an autosave.
                .onFocusChanged { if (!it.isFocused) onCommit?.invoke() },
        )
    }
}

@Composable
private fun FileChip(name: String, meta: String, tint: Color, onRemove: (() -> Unit)?) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .hbGlass(shape = HbGlassShape.Tile, state = HbGlassState.Default)
            .padding(horizontal = 9.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        HbGlyphs.File(tint, 12.dp)
        Spacer(Modifier.width(7.dp))
        HbText(name, style = HbType.readout.copy(fontSize = 11.sp), color = tint, maxLines = 1, modifier = Modifier.weight(1f))
        if (meta.isNotBlank()) {
            HbText(meta, style = HbType.readout.copy(fontSize = 10.sp), color = palette.textFaint)
        }
        if (onRemove != null) {
            Spacer(Modifier.width(6.dp))
            Box(Modifier.size(22.dp).clickable(onClick = onRemove), Alignment.Center) {
                HbGlyphs.Close(palette.iconDim, 10.dp)
            }
        }
    }
}

@Composable
private fun EmptyState(title: String, blurb: String) {
    val palette = LocalHbPalette.current
    Column(
        Modifier.fillMaxSize().padding(horizontal = 32.dp, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        HbText(
            title,
            style = HbType.headerBar.copy(fontSize = 11.sp, letterSpacing = 0.16.em),
            color = palette.iconDim,
            caps = true,
        )
        Spacer(Modifier.height(8.dp))
        HbText(blurb, style = HbType.read.copy(fontSize = 12.5.sp), color = palette.textFaint)
    }
}

private fun fmtBytes(n: Long): String = when {
    n <= 0 -> ""
    n < 1024 -> "$n B"
    n < 1024 * 1024 -> "${n / 1024} KB"
    else -> "${"%.1f".format(n / (1024.0 * 1024.0))} MB"
}
