// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.settings

import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.health.connect.client.PermissionController
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.health.HealthConnectSource
import com.speda.heartbreaker.health.HealthSyncManager
import com.speda.heartbreaker.health.HealthType
import com.speda.heartbreaker.i18n.AppStrings
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Atomix's signature green — the health surfaces tint to the agent that owns
 *  the data, not to whichever agent happens to be selected. */
private val AtomixGreen = Color(0xFF3FAE74)

/**
 * Settings ▸ HEALTH (docs/ATOMIX_HEALTH_SYNC.md §1.1).
 *
 * The design's rule is "one honest consent moment, then silence": the toggle
 * hands straight to Health Connect's OWN system permission sheet — never a
 * custom dialog dressed up as one — and after that the tab is just a last-sync
 * line and two buttons.
 */
@Composable
fun HealthTab(config: AppConfig, graph: AppGraph) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    // HealthSyncManager is plain Kotlin, not @Composable — keep it in sync.
    graph.healthSync.strings = t

    var state by remember { mutableStateOf<HealthSyncManager.SyncState?>(null) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf("") }
    var isError by remember { mutableStateOf(false) }
    var serverSamples by remember { mutableStateOf<Int?>(null) }

    suspend fun refresh() {
        state = graph.healthSync.state()
        serverSamples = graph.api.healthStatus(config)?.samples
    }

    LaunchedEffect(Unit) { refresh() }

    // Health Connect's own permission sheet. The OS renders it, lists exactly
    // the record types we ask for, and its answer is the source of truth.
    val permissionLauncher = rememberLauncherForActivityResult(
        PermissionController.createRequestPermissionResultContract(),
    ) { granted ->
        scope.launch {
            if (granted.isEmpty()) {
                message = t.settingsHealth.nothingGranted
                isError = true
                graph.settings.setHealthEnabled(false)
            } else {
                graph.settings.setHealthEnabled(true)
                graph.healthSync.ensureScheduled()
                busy = true
                message = t.settingsHealth.backfilling(HealthSyncManager.BACKFILL_DAYS)
                isError = false
                message = describe(graph.healthSync.sync(config), t)
                busy = false
            }
            refresh()
        }
    }

    val s = state
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        SectionHeader(t.settingsHealth.title)
        Panel {
            when (s?.availability) {
                HealthConnectSource.Availability.UNSUPPORTED -> {
                    Hint(t.settingsHealth.unsupported)
                }
                HealthConnectSource.Availability.NOT_INSTALLED -> {
                    // Deep-link rather than fail silently (§2, "UX details that matter").
                    Hint(t.settingsHealth.notInstalled)
                    Spacer(Modifier.height(10.dp))
                    SettingsButton(
                        t.settingsHealth.getHealthConnect,
                        onClick = { openUrl(context, HealthConnectSource.PLAY_LISTING) },
                        tint = AtomixGreen,
                    )
                }
                else -> {
                    ToggleRow(
                        label = t.settingsHealth.syncToggleLabel,
                        subtitle = t.settingsHealth.syncToggleHint,
                        checked = s?.enabled == true,
                        enabled = !busy && s != null,
                    ) { on ->
                        scope.launch {
                            if (on) {
                                val types = s?.selectedTypes ?: emptySet()
                                permissionLauncher.launch(graph.healthSync.source.permissionsFor(types))
                            } else {
                                // Stop reading and syncing. Grants stay — only the
                                // OS sheet can revoke those, and pretending
                                // otherwise would be a lie about who holds them.
                                graph.settings.setHealthEnabled(false)
                                graph.healthSync.cancelSchedule()
                                message = t.settingsHealth.syncPaused
                                isError = false
                                refresh()
                            }
                        }
                    }
                }
            }
        }

        if (s != null && s.availability == HealthConnectSource.Availability.AVAILABLE) {
            SectionHeader(t.settingsHealth.dataTypes)
            Panel {
                Hint(t.settingsHealth.dataTypesHint)
                Spacer(Modifier.height(8.dp))
                HealthType.entries.forEach { type ->
                    val selected = type in s.selectedTypes
                    TypeRow(
                        label = healthTypeLabel(type, t),
                        checked = selected,
                        granted = type in s.grantedTypes,
                        enabled = !busy,
                        notGrantedLabel = t.settingsHealth.notGranted,
                    ) { on ->
                        scope.launch {
                            val next = if (on) s.selectedTypes + type else s.selectedTypes - type
                            graph.settings.setHealthTypes(next.map { it.key }.toSet())
                            // A newly checked type needs its own grant before it
                            // can ever produce data.
                            if (on && s.enabled) {
                                permissionLauncher.launch(graph.healthSync.source.permissionsFor(setOf(type)))
                            }
                            refresh()
                        }
                    }
                }
                Spacer(Modifier.height(6.dp))
                SettingsButton(
                    t.settingsHealth.manageGrants,
                    onClick = {
                        runCatching {
                            context.startActivity(
                                Intent(HealthConnectSource.ACTION_HEALTH_CONNECT_SETTINGS)
                                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                            )
                        }
                    },
                )
            }

            SectionHeader(t.settingsHealth.syncSection)
            Panel {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusDot(ok = s.lastSyncMillis > 0L)
                    HbText(
                        if (s.lastSyncMillis > 0L) t.settingsHealth.lastSync(formatStamp(s.lastSyncMillis)) else t.settingsHealth.neverSynced,
                        style = HbType.read.copy(fontSize = 13.5.sp),
                        color = palette.textDim,
                    )
                    serverSamples?.let {
                        HbText(t.settingsHealth.onServer(it), style = HbType.read.copy(fontSize = 13.5.sp), color = palette.textFaint)
                    }
                }
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SettingsButton(
                        if (busy) t.settingsHealth.syncing else t.settingsHealth.syncNow,
                        onClick = {
                            scope.launch {
                                busy = true; message = ""; isError = false
                                val result = graph.healthSync.sync(config)
                                message = describe(result, t)
                                isError = result is HealthSyncManager.Result.Failed ||
                                    result is HealthSyncManager.Result.NotPermitted
                                busy = false
                                refresh()
                            }
                        },
                        enabled = !busy && s.enabled,
                        tint = AtomixGreen,
                    )
                    SettingsButton(
                        t.settingsHealth.disconnectWipe,
                        onClick = {
                            scope.launch {
                                busy = true
                                val ok = graph.healthSync.disconnectAndWipe(config)
                                message = if (ok) t.settingsHealth.disconnected else t.settingsHealth.disconnectFailed
                                isError = !ok
                                busy = false
                                refresh()
                            }
                        },
                        enabled = !busy,
                        tint = palette.red,
                    )
                }
                if (message.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    HbText(
                        message,
                        style = HbType.readout.copy(fontSize = 11.sp),
                        color = if (isError) palette.red else palette.textDim,
                    )
                }
            }
        }

        Spacer(Modifier.height(24.dp))
    }
}

/** Checkbox row that also reports whether the OS has actually granted the type —
 *  the checkbox is our intent, the badge is reality. */
@Composable
private fun TypeRow(
    label: String,
    checked: Boolean,
    granted: Boolean,
    enabled: Boolean,
    notGrantedLabel: String,
    onToggle: (Boolean) -> Unit,
) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 5.dp)
            .then(if (enabled) Modifier.clickable { onToggle(!checked) } else Modifier),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Box(
            Modifier
                .size(18.dp)
                .background(
                    if (checked) AtomixGreen.copy(alpha = 0.30f) else Color.Transparent,
                    RoundedCornerShape(4.dp),
                )
                .border(
                    1.dp,
                    if (checked) AtomixGreen.copy(alpha = 0.7f) else palette.edge,
                    RoundedCornerShape(4.dp),
                ),
            contentAlignment = Alignment.Center,
        ) {
            if (checked) Box(Modifier.size(8.dp).background(AtomixGreen, RoundedCornerShape(2.dp)))
        }
        HbText(label, style = HbType.read.copy(fontSize = 14.sp), color = palette.text, modifier = Modifier.weight(1f))
        if (checked && !granted) {
            HbText(notGrantedLabel, style = HbType.read.copy(fontSize = 13.sp), color = palette.amberBright)
        }
    }
}

/** [HealthType.label] is a fixed English default (the enum can't read
 *  `LocalStrings`); resolved to the display string here instead. */
private fun healthTypeLabel(type: HealthType, t: AppStrings): String = when (type) {
    HealthType.Steps -> t.settingsHealth.typeSteps
    HealthType.Sleep -> t.settingsHealth.typeSleep
    HealthType.HeartRate -> t.settingsHealth.typeHeartRate
    HealthType.Exercise -> t.settingsHealth.typeExercise
    HealthType.Weight -> t.settingsHealth.typeWeight
    HealthType.OxygenSaturation -> t.settingsHealth.typeOxygen
}

private fun describe(result: HealthSyncManager.Result, t: AppStrings): String = when (result) {
    is HealthSyncManager.Result.Synced -> t.settingsHealth.syncedRecords(result.read, result.accepted, result.duplicates)
    HealthSyncManager.Result.NothingNew -> t.settingsHealth.upToDate
    HealthSyncManager.Result.NotPermitted -> t.settingsHealth.notPermitted
    is HealthSyncManager.Result.Failed -> result.message
}

private fun formatStamp(millis: Long): String =
    SimpleDateFormat("d MMM HH:mm", Locale.getDefault()).format(Date(millis))
