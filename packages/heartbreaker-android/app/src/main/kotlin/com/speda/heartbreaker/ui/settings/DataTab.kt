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
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun DataTab(config: AppConfig, graph: AppGraph) {
    val palette = LocalHbPalette.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var importing by remember { mutableStateOf(false) }
    var importMsg by remember { mutableStateOf("") }
    var importErr by remember { mutableStateOf(false) }

    var indexing by remember { mutableStateOf(false) }
    var indexMsg by remember { mutableStateOf("") }

    val pickZip = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        importing = true; importErr = false; importMsg = "Uploading & starting import…"
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
                importing = false; importErr = true; importMsg = "Couldn't read that file."
                return@launch
            }
            val msg = graph.api.importChats(config, name, bytes)
            importing = false
            importErr = msg.startsWith("Import failed")
            importMsg = msg
        }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        SectionHeader("Import conversations")
        Panel {
            Hint("Upload the .zip from your Claude data export. Each conversation becomes a session with its original dates. Runs in the background — sessions appear as they process.")
            Spacer(Modifier.height(10.dp))
            SettingsButton(
                if (importing) "Importing…" else "Choose .zip…",
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

        SectionHeader("Index past conversations")
        Panel {
            Hint("One-time: mine durable facts about you from your whole history, consolidate them, and write a profile to memory so SPEDA actually knows you. Background; a couple of minutes.")
            Spacer(Modifier.height(10.dp))
            SettingsButton(
                if (indexing) "Indexing…" else "Index history",
                onClick = {
                    indexing = true; indexMsg = "Indexing started…"
                    scope.launch {
                        val msg = graph.api.indexHistory(config)
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
