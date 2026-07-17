package com.speda.heartbreaker.ui.chat

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.Attachments
import com.speda.heartbreaker.data.DocBlock
import com.speda.heartbreaker.data.ImageBlock
import com.speda.heartbreaker.data.ModelInfo
import kotlinx.coroutines.launch
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.ui.HbText
import java.util.Locale

/** Photo-picker cap — the contract requires more than one. */
private const val MAX_ATTACHMENTS = 10

/**
 * The composer — port of InputBar.tsx at the mobile breakpoint: an auto-grow
 * field, the "+" overflow (attach / budget / voice land in a later pass), the
 * model picker keeping its own slot, the send/stop control, and the status strip.
 */
@Composable
fun Composer(
    isStreaming: Boolean,
    agentName: String,
    models: List<ModelInfo>,
    model: String,
    onModelChange: (String) -> Unit,
    onSend: (String, List<ImageBlock>, List<DocBlock>) -> Unit,
    onStop: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("") }
    var pickerOpen by remember { mutableStateOf(false) }
    var plusOpen by remember { mutableStateOf(false) }

    // Staged attachments, cleared on send.
    val images = remember { mutableStateListOf<ImageBlock>() }
    val docs = remember { mutableStateListOf<DocBlock>() }
    var busy by remember { mutableStateOf(false) }

    // Android's photo picker: no permission, no gallery scopes — this is the bit
    // that's genuinely nicer than the desktop's file dialog.
    val pickPhotos = rememberLauncherForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(MAX_ATTACHMENTS),
    ) { uris ->
        if (uris.isNotEmpty()) {
            busy = true
            scope.launch {
                uris.forEach { uri -> Attachments.imageBlock(context, uri)?.let(images::add) }
                busy = false
            }
        }
    }

    // Anything else goes through SAF as a document block; the backend extracts
    // the text server-side, so no client parsing and it works for every provider.
    val pickFiles = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments(),
    ) { uris ->
        if (uris.isNotEmpty()) {
            busy = true
            scope.launch {
                uris.forEach { uri ->
                    if (Attachments.isImage(context, uri)) {
                        Attachments.imageBlock(context, uri)?.let(images::add)
                    } else {
                        Attachments.docBlock(context, uri)?.let(docs::add)
                    }
                }
                busy = false
            }
        }
    }

    val canSend = (text.isNotBlank() || images.isNotEmpty() || docs.isNotEmpty()) && !busy

    fun submit() {
        if (!canSend) return
        onSend(text.trim(), images.toList(), docs.toList())
        text = ""
        images.clear()
        docs.clear()
    }

    Column(modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 6.dp)) {
        Box {
            Column(
                Modifier
                    .fillMaxWidth()
                    .hbGlass(shape = HbGlassShape.R14)
                    .padding(horizontal = 12.dp, vertical = 10.dp),
            ) {
                // Staged attachments — tap one to drop it before sending.
                if (images.isNotEmpty() || docs.isNotEmpty() || busy) {
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState())
                            .padding(bottom = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        images.forEachIndexed { i, img ->
                            val bmp = rememberDataUrlImage(img.asDataUrl())
                            Box(
                                Modifier
                                    .size(46.dp)
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(palette.accent.copy(alpha = 0.10f))
                                    .clickable { images.removeAt(i) },
                            ) {
                                if (bmp != null) {
                                    Image(
                                        bitmap = bmp,
                                        contentDescription = null,
                                        contentScale = ContentScale.Crop,
                                        modifier = Modifier.matchParentSize(),
                                    )
                                }
                            }
                        }
                        docs.forEachIndexed { i, d ->
                            Row(
                                Modifier
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(palette.accent.copy(alpha = 0.08f))
                                    .border(1.dp, palette.accent.copy(alpha = 0.28f), RoundedCornerShape(8.dp))
                                    .clickable { docs.removeAt(i) }
                                    .padding(horizontal = 8.dp, vertical = 6.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(5.dp),
                            ) {
                                HbText(
                                    d.name,
                                    style = HbType.read.copy(fontSize = 11.sp),
                                    color = palette.text,
                                    maxLines = 1,
                                    modifier = Modifier.widthIn(max = 130.dp),
                                )
                                HbGlyphs.Close(palette.iconDim, size = 10.dp)
                            }
                        }
                        if (busy) {
                            HbText("…", style = HbType.read, color = palette.textFaint)
                        }
                    }
                }

                BasicTextField(
                    value = text,
                    onValueChange = { text = it },
                    textStyle = HbType.read.merge(TextStyle(color = palette.text)),
                    cursorBrush = SolidColor(palette.accentBright),
                    maxLines = 6,
                    modifier = Modifier.fillMaxWidth(),
                    decorationBox = { inner ->
                        // The text sits VERTICALLY CENTRED in the field, as it does
                        // in the web. Pinning it to the top of the min-height box
                        // left it riding high with dead space underneath.
                        Box(
                            Modifier.fillMaxWidth().heightIn(min = 44.dp),
                            contentAlignment = Alignment.CenterStart,
                        ) {
                            if (text.isEmpty()) {
                                HbText(
                                    "How can I help you today?",
                                    style = HbType.read.copy(fontSize = 16.sp),
                                    color = palette.textFaint,
                                )
                            }
                            inner()
                        }
                    },
                )

                Spacer(Modifier.size(8.dp))

                Row(verticalAlignment = Alignment.CenterVertically) {
                    // "+" overflow — attach sources. Budget/voice join it later.
                    Box(
                        Modifier
                            .size(30.dp)
                            .clip(CircleShape)
                            .hbGlass(shape = HbGlassShape.Pill, state = if (plusOpen) HbGlassState.Active else HbGlassState.Default)
                            .clickable { plusOpen = !plusOpen },
                        contentAlignment = Alignment.Center,
                    ) { HbGlyphs.Plus(if (plusOpen) palette.accentBright else palette.iconBright, size = 14.dp) }

                    Spacer(Modifier.weight(1f))

                    // Model picker keeps its slot on mobile.
                    Row(
                        Modifier
                            .hbGlass(shape = HbGlassShape.Pill, state = if (pickerOpen) HbGlassState.Active else HbGlassState.Default)
                            .clickable { pickerOpen = !pickerOpen }
                            .padding(horizontal = 10.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(5.dp),
                    ) {
                        HbText(
                            (model.ifEmpty { "MODEL" }).uppercase(Locale.ENGLISH),
                            style = HbType.label.copy(fontSize = 9.5.sp),
                            color = palette.textDim,
                            maxLines = 1,
                            modifier = Modifier.widthIn(max = 150.dp),
                        )
                        HbGlyphs.ChevronDown(palette.iconDim, size = 8.dp)
                    }

                    Spacer(Modifier.width(8.dp))

                    // Send / stop
                    Box(
                        Modifier
                            .size(32.dp)
                            .clip(CircleShape)
                            .hbGlass(
                                shape = HbGlassShape.Pill,
                                state = when {
                                    isStreaming -> HbGlassState.Amber
                                    canSend -> HbGlassState.Active
                                    else -> HbGlassState.Default
                                },
                            )
                            .clickable { if (isStreaming) onStop() else submit() },
                        contentAlignment = Alignment.Center,
                    ) {
                        if (isStreaming) {
                            Box(Modifier.size(10.dp).background(palette.amberBright))
                        } else {
                            HbGlyphs.ArrowUp(if (canSend) palette.accentBright else palette.textFaint, size = 15.dp)
                        }
                    }
                }
            }

            if (pickerOpen) {
                ModelPicker(
                    models = models,
                    current = model,
                    onPick = { pickerOpen = false; onModelChange(it) },
                )
            }

            if (plusOpen) {
                Column(
                    Modifier
                        .align(Alignment.BottomStart)
                        .padding(bottom = 52.dp)
                        .widthIn(min = 180.dp)
                        .hbGlass(shape = HbGlassShape.R12, state = HbGlassState.Menu),
                ) {
                    AttachItem("Photos") {
                        plusOpen = false
                        pickPhotos.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
                    }
                    AttachItem("Files") {
                        plusOpen = false
                        pickFiles.launch(arrayOf("*/*"))
                    }
                }
            }
        }

        // Status strip — centred, as InputBar.tsx does (justifyContent: center).
        // The web's other segments (Enter to send / Shift+Enter / paste or drop)
        // are keyboard-and-pointer affordances that don't apply on a phone.
        Row(
            Modifier.fillMaxWidth().padding(top = 6.dp),
            horizontalArrangement = Arrangement.Center,
        ) {
            HbText(
                "$agentName can make mistakes",
                style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.06.em),
                color = palette.iconDim,
            )
        }
    }
}

/** One row of the "+" attach menu. */
@Composable
private fun AttachItem(label: String, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(9.dp),
    ) {
        HbGlyphs.Plus(palette.iconDim, size = 12.dp)
        HbText(
            label,
            style = HbType.headerBar.copy(fontSize = 12.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.12.em),
            color = palette.iconBright,
            caps = true,
        )
    }
}

/** Provider-grouped glass dropdown of routable models. */
@Composable
private fun ModelPicker(models: List<ModelInfo>, current: String, onPick: (String) -> Unit) {
    val palette = LocalHbPalette.current
    Column(
        Modifier
            .padding(bottom = 52.dp)
            .fillMaxWidth()
            .hbGlass(shape = HbGlassShape.R12, state = HbGlassState.Menu),
    ) {
        if (models.isEmpty()) {
            HbText(
                "// no models reported",
                style = HbType.readout.copy(fontSize = 10.sp),
                color = palette.textFaint,
                modifier = Modifier.padding(12.dp),
            )
            return@Column
        }
        LazyColumn(Modifier.heightIn(max = 260.dp)) {
            items(models, key = { it.id }) { m ->
                val active = m.id == current
                Row(
                    Modifier
                        .fillMaxWidth()
                        .background(if (active) palette.accent.copy(alpha = 0.12f) else Color.Transparent)
                        .clickable { onPick(m.id) }
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Column(Modifier.weight(1f)) {
                        HbText(
                            m.name.ifEmpty { m.id },
                            style = HbType.headerBar.copy(fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.1.em),
                            color = if (active) Color.White else palette.textDim,
                            caps = true,
                            maxLines = 1,
                        )
                        if (m.provider != null) {
                            HbText(
                                m.provider,
                                style = HbType.readout.copy(fontSize = 9.sp),
                                color = palette.textFaint,
                                caps = true,
                                maxLines = 1,
                            )
                        }
                    }
                }
            }
        }
    }
}
