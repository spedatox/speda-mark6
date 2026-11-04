// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.projects

import java.util.UUID
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
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
import androidx.compose.ui.platform.LocalUriHandler
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
    var creatingCloud by remember { mutableStateOf(false) }

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

    // A full-screen SHEET, mounted over the chat by ChatScreen — so it has to
    // occlude and it has to swallow. Without the opaque fill the transcript
    // reads straight through this screen; without the no-op clickable, taps on
    // any gap between rows fall through to whatever is under them (the
    // composer, a message action). Both are exactly what SettingsScreen does,
    // and this pane is mounted the same way it is.
    Column(
        modifier
            .fillMaxSize()
            .background(palette.base)
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null,
            ) {}
            .statusBarsPadding()
            .navigationBarsPadding()
            // The instructions, description and name fields all live in here, so
            // the sheet has to lift off the keyboard or they open underneath it.
            // Same pairing SettingsScreen and its tabs use.
            .imePadding(),
    ) {
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
                PillButton(t.projects.newProject, palette.accentBright) { creatingCloud = false; creating = !creating }
                Spacer(Modifier.width(4.dp))
                Box(Modifier.size(28.dp).clickable(onClick = onClose), Alignment.Center) {
                    HbGlyphs.Close(palette.iconDim, 13.dp)
                }
            }

            Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp), horizontalArrangement = Arrangement.End) {
                PillButton("New cloud project", palette.accentBright) { creating = false; creatingCloud = !creatingCloud }
            }
            if (creatingCloud) {
                CloudEnvironmentForm(config, api, onCancel = { creatingCloud = false }) { projectId ->
                    scope.launch {
                        reload()
                        creatingCloud = false
                        openId = projectId
                    }
                }
            }
            if (creating) {
                NewProjectForm(
                    t = t,
                    onCancel = { creating = false },
                    onCreate = { name, description, instructions, icon, path ->
                        scope.launch {
                            val made = api.createProject(config, name, description, instructions, icon, path)
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
    onCreate: (name: String, description: String, instructions: String, icon: String, path: String) -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }
    var instructions by remember { mutableStateOf("") }
    var icon by remember { mutableStateOf("") }
    var path by remember { mutableStateOf("") }
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
        Field(path, { path = it }, "Project folder on execution host", Modifier.fillMaxWidth())
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
                if (name.isNotBlank() && path.isNotBlank()) onCreate(name, description, instructions, icon, path)
            }
        }
    }
    Spacer(Modifier.height(10.dp))
}

private fun JsonObject.text(key: String): String =
    (this[key] as? kotlinx.serialization.json.JsonPrimitive)?.contentOrNull.orEmpty()

private fun JsonObject.objects(key: String): List<JsonObject> =
    (this[key] as? JsonArray)?.mapNotNull { it as? JsonObject }.orEmpty()

@Composable
private fun CloudEnvironmentForm(
    config: AppConfig,
    api: IgorApi,
    project: Project? = null,
    previous: JsonObject? = null,
    onCancel: () -> Unit,
    onPrepared: (Int) -> Unit,
) {
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf(project?.name.orEmpty()) }
    var repo by remember { mutableStateOf(previous?.text("repository").orEmpty()) }
    var branch by remember { mutableStateOf(previous?.text("branch") ?: "main") }
    var check by remember { mutableStateOf(previous?.text("check") ?: "git --version") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    val requestId = remember(name, repo, branch, check) { UUID.randomUUID().toString() }
    Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (project == null) Field(name, { name = it }, "Project name", Modifier.fillMaxWidth())
        Field(repo, { repo = it }, "GitHub owner/repository", Modifier.fillMaxWidth())
        Field(branch, { branch = it }, "Base branch", Modifier.fillMaxWidth())
        Field(check, { check = it }, "Environment check", Modifier.fillMaxWidth(), singleLine = false)
        if (error.isNotBlank()) HbText(error, style = HbType.read, color = palette.amber)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            PillButton("Cancel", palette.iconDim, onCancel)
            PillButton(if (busy) "Preparing…" else "Prepare environment", palette.accentBright) {
                if (!busy && name.isNotBlank() && repo.isNotBlank() && branch.isNotBlank() && check.isNotBlank()) {
                    busy = true
                    error = ""
                    scope.launch {
                        try {
                            val path = project?.let { "/${it.id}/cloud/environments" } ?: "/cloud/environments"
                            val result = api.projectRequest(config, path, buildJsonObject {
                                put("request_id", requestId)
                                put("agent_id", config.agentId)
                                put("name", name.trim())
                                put("repository", repo.trim())
                                put("branch", branch.trim())
                                put("check", check)
                            })
                            onPrepared(result.text("project_id").toInt())
                        } catch (exception: kotlinx.coroutines.CancellationException) {
                            throw exception
                        } catch (exception: Exception) {
                            error = exception.message ?: "Could not prepare environment"
                        } finally {
                            busy = false
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun CloudProjectDetail(config: AppConfig, api: IgorApi, project: Project, onPublished: (Boolean) -> Unit) {
    val palette = LocalHbPalette.current
    val scope = rememberCoroutineScope()
    val uriHandler = LocalUriHandler.current
    var cloud by remember(project.id) { mutableStateOf<JsonObject?>(null) }
    var workspace by remember(project.id) { mutableStateOf<JsonObject?>(null) }
    var changes by remember(project.id) { mutableStateOf<JsonObject?>(null) }
    var title by remember(project.id) { mutableStateOf("") }
    var error by remember(project.id) { mutableStateOf("") }
    var refresh by remember(project.id) { mutableStateOf(0) }
    var preparing by remember(project.id) { mutableStateOf(false) }
    var busy by remember(project.id) { mutableStateOf(false) }
    val base = "/${project.id}"

    LaunchedEffect(config, project.id, refresh) {
        try {
            do {
                val result = api.projectRequest(config, "$base/cloud")
                cloud = result
                val environments = result.objects("environments")
                onPublished(environments.any { it.text("status") == "published" })
                workspace = api.projectRequest(config, "$base/workspace")
                val active = environments.any { it.text("status") == "preparing" } ||
                    result.objects("pull_requests").any { it.text("status") == "executing" }
                if (active) delay(2000)
            } while (active)
        } catch (exception: kotlinx.coroutines.CancellationException) {
            throw exception
        } catch (exception: Exception) {
            error = exception.message ?: "Could not load project environment"
        }
    }

    fun action(block: suspend () -> Unit) {
        if (busy) return
        busy = true
        error = ""
        scope.launch {
            try {
                block()
            } catch (exception: kotlinx.coroutines.CancellationException) {
                throw exception
            } catch (exception: Exception) {
                error = exception.message ?: "Project action failed"
            } finally {
                busy = false
            }
        }
    }

    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        SectionLabel("Cloud environment")
        if (error.isNotBlank()) HbText(error, style = HbType.read, color = palette.amber)
        val environments = cloud?.objects("environments").orEmpty()
        for (environment in environments) {
            HbText("${environment.text("repository")} · ${environment.text("branch")} · ${environment.text("status")}",
                style = HbType.read, color = palette.text)
            val commit = environment.text("commit")
            if (commit.isNotEmpty()) HbText("Commit ${commit.take(12)}", style = HbType.read, color = palette.textDim)
            val output = (environment["check_result"] as? JsonObject)?.text("output").orEmpty()
            if (output.isNotBlank()) HbText(output, style = HbType.read, color = palette.textDim)
            if (environment.text("error").isNotBlank()) HbText(environment.text("error"), style = HbType.read, color = palette.amber)
            if (environment.text("status") == "ready") {
                PillButton("Publish environment", palette.accentBright) {
                    action {
                        api.projectRequest(config, "$base/cloud/publish", buildJsonObject {
                            put("environment_id", environment.text("id"))
                            put("review_hash", environment.text("review_hash"))
                        })
                        refresh += 1
                    }
                }
            }
        }
        if (preparing) {
            CloudEnvironmentForm(config, api, project, environments.firstOrNull(), onCancel = { preparing = false }) {
                preparing = false
                refresh += 1
            }
        } else {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                PillButton("Prepare new revision", palette.accentBright) { preparing = true }
                PillButton("Refresh", palette.iconDim) { refresh += 1 }
            }
        }
        for (task in workspace?.objects("tasks").orEmpty()) {
            val label = task.text("task")
            HbText("$label · ${task.text("status")}", style = HbType.read, color = palette.textDim)
            if (task.text("status") == "succeeded") PillButton("Review changes", palette.accentBright) {
                action {
                    changes = api.projectRequest(config, "$base/changes/${task.text("id")}")
                    title = label.take(200)
                }
            }
        }
        changes?.let { review ->
            HbText(review.text("diff"), style = HbType.read, color = palette.textDim)
            Field(title, { title = it }, "Pull request title", Modifier.fillMaxWidth())
            PillButton("Open draft pull request", palette.accentBright) {
                if (title.isNotBlank() && review.objects("changes").isNotEmpty()) action {
                    api.projectRequest(config, "$base/cloud/pull-requests", buildJsonObject {
                        put("task_id", review.text("task_id"))
                        put("title", title.trim())
                        put("expected_source_revision", review.text("expected_source_revision"))
                        put("expected_artifact_revision", review.text("expected_artifact_revision"))
                    })
                    changes = null
                    refresh += 1
                }
            }
        }
        for (pr in cloud?.objects("pull_requests").orEmpty()) {
            val url = pr.text("url")
            if (url.startsWith("https://github.com/")) {
                PillButton(pr.text("title"), palette.accentBright) { uriHandler.openUri(url) }
            } else {
                HbText("${pr.text("title")} · ${pr.text("status")}", style = HbType.read, color = palette.textDim)
            }
            if (pr.text("status") == "reconciling") PillButton("Check GitHub result", palette.iconDim) {
                action {
                    api.projectRequest(config, "$base/cloud/pull-requests/${pr.text("id")}/reconcile", buildJsonObject {})
                    refresh += 1
                }
            }
        }
    }
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
    var cloudPublished by remember(project.id) { mutableStateOf(!project.cloud) }

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
                if (cloudPublished && !project.archived) {
                    PillButton(t.projects.newChatHere, palette.accentBright, onNewChat)
                }
            }
        }

        if (project.cloud) item {
            CloudProjectDetail(config, api, project) { cloudPublished = it }
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
