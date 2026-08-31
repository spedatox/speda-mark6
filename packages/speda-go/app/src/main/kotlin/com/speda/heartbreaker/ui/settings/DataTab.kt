// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.settings

import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
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
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun DataTab(config: AppConfig, graph: AppGraph) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var importing by remember { mutableStateOf(false) }
    var importMsg by remember { mutableStateOf("") }
    var importErr by remember { mutableStateOf(false) }

    var indexing by remember { mutableStateOf(false) }
    var indexMsg by remember { mutableStateOf("") }

    val pickZip = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        importing = true; importErr = false; importMsg = t.settingsData.uploadingStarting
        scope.launch {
            val name = withContext(Dispatchers.IO) {
                context.contentResolver.query(uri, null, null, null, null)?.use { c ->
                    val i = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if (i >= 0 && c.moveToFirst()) c.getString(i) else null
                } ?: (uri.lastPathSegment ?: "export.zip")
            }
            val bytes = withContext(Dispatchers.IO) {
                runCatching { context.contentResolver.openInputStream(uri)?.use { it.readBytes() } }.getOrNull()
            }
            if (bytes == null || bytes.isEmpty()) {
                importing = false; importErr = true; importMsg = t.settingsData.couldntRead
                return@launch
            }
            val outcome = graph.api.importChats(config, name, bytes, t)
            importing = false
            importErr = !outcome.ok
            importMsg = outcome.message
        }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        SectionHeader(t.settingsData.importTitle)
        Panel {
            Hint(t.settingsData.importDesc)
            Spacer(Modifier.height(10.dp))
            SettingsButton(
                if (importing) t.settingsData.importing else t.settingsData.chooseZip,
                onClick = { pickZip.launch(arrayOf("application/zip", "application/octet-stream")) },
                enabled = !importing,
            )
            if (importMsg.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                HbText(
                    importMsg,
                    style = HbType.readout.copy(fontSize = 11.sp),
                    color = if (importErr) palette.red else palette.textDim,
                )
            }
        }

        SectionHeader(t.settingsData.indexTitle)
        Panel {
            Hint(t.settingsData.indexDesc)
            Spacer(Modifier.height(10.dp))
            SettingsButton(
                if (indexing) t.settingsData.indexing else t.settingsData.indexHistory,
                onClick = {
                    indexing = true; indexMsg = t.settingsData.indexingStarted
                    scope.launch {
                        val msg = graph.api.indexHistory(config, t)
                        indexing = false; indexMsg = msg
                    }
                },
                enabled = !indexing,
            )
            if (indexMsg.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                HbText(indexMsg, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textDim)
            }
        }

        Spacer(Modifier.height(24.dp))
    }
}
