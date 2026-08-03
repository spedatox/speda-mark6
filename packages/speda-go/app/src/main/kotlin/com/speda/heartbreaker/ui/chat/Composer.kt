package com.speda.heartbreaker.ui.chat

import android.content.Intent
import android.speech.RecognizerIntent
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
 * field, the "+" overflow (attach sources, budget mode, dictation), the model
 * picker keeping its own slot, the send/stop control, and the status strip.
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
    /** Null until the backend has answered — the row shows as unknown, not as
     *  "full power", so the owner is never told the wrong spend state. */
    budgetMode: Boolean?,
    onBudgetToggle: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("") }
    var pickerOpen by remember { mutableStateOf(false) }
    var plusOpen by remember { mutableStateOf(false) }

    // Dictation. Routed through the platform's own recognizer activity rather
    // than a raw SpeechRecognizer: the system dialog carries the mic permission,
    // the listening UI and the partial-results display, so the app declares no
    // RECORD_AUDIO and there is no half-built recording surface to maintain.
    val dictate = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val said = result.data
            ?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
            ?.firstOrNull()
            ?.trim()
            .orEmpty()
        if (said.isNotEmpty()) {
            text = if (text.isBlank()) said else "${text.trimEnd()} $said"
        }
    }

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
                    // Dictation — the transcript is APPENDED to whatever is
                    // already typed, never replaces it (InputBar.tsx does the
                    // same): speaking a second clause must not wipe the first.
                    AttachItem("Voice input") {
                        plusOpen = false
                        runCatching { dictate.launch(speechIntent()) }
                    }
                    // Budget mode. Optimistic in the UI and re-synced after each
                    // turn by the caller, because SPEDA can flip it itself.
                    AttachItem(
                        label = when (budgetMode) {
                            true -> "Budget mode · FRUGAL"
                            false -> "Budget mode · FULL"
                            null -> "Budget mode · —"
                        },
                        tint = when (budgetMode) {
                            true -> palette.green
                            false -> palette.amberBright
                            null -> null
                        },
                    ) {
                        plusOpen = false
                        onBudgetToggle()
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

/**
 * The dictation intent. `FREE_FORM` because the owner is talking to an
 * assistant, not filling a field, and the locale follows the device — the same
 * "speak in whatever you speak" rule the desktop's recognizer uses.
 */
private fun speechIntent(): Intent =
    Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
        putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
    }

/** One row of the "+" attach menu. */
@Composable
private fun AttachItem(label: String, tint: Color? = null, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(9.dp),
    ) {
        HbGlyphs.Plus(tint ?: palette.iconDim, size = 12.dp)
        HbText(
            label,
            style = HbType.headerBar.copy(fontSize = 12.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.12.em),
            color = tint ?: palette.iconBright,
            caps = true,
        )
    }
}

/** Provider display names — mirrors PROVIDER_LABELS in InputBar.tsx. */
private val PROVIDER_LABELS = mapOf(
    "anthropic" to "ANTHROPIC",
    "openai" to "OPENAI",
    "gemini" to "GOOGLE GEMINI",
    "zai" to "Z.AI · GLM",
    "deepseek" to "DEEPSEEK",
    "nvidia" to "NVIDIA NIM",
    "ollama" to "DEAD ZONE PROTOCOL",
)

private val MODEL_ID_PREFIX = Regex("^(anthropic|openai|gemini|zai|deepseek|nvidia|ollama):")
private val CLAUDE_PREFIX = Regex("^Claude\\s+", RegexOption.IGNORE_CASE)

/** The web's shortModelName — drop the provider prefix and the "Claude " noise. */
private fun shortModelName(name: String): String =
    name.replace(MODEL_ID_PREFIX, "").replace(CLAUDE_PREFIX, "")

/** Old backends report no provider; everything routed then was Anthropic. */
private fun ModelInfo.providerKey(): String =
    provider?.takeIf { it.isNotBlank() } ?: "anthropic"

/**
 * Provider-grouped glass dropdown of routable models — a single-expand accordion.
 * The catalogue runs to a hundred-odd models across seven providers, which on a
 * phone is minutes of scrolling, so only provider headers are listed and exactly
 * one group is ever open: the one holding the pinned model, or whichever the owner
 * taps. Opening a group shuts the previous one, so the sheet never grows past a
 * screen.
 */
@Composable
private fun ModelPicker(models: List<ModelInfo>, current: String, onPick: (String) -> Unit) {
    val palette = LocalHbPalette.current

    // Provider order follows the backend's catalogue order, as the web does.
    val groups = remember(models) {
        models.groupBy { it.providerKey() }.toList()
    }
    val currentProvider = remember(models, current) {
        models.firstOrNull { it.id == current }?.providerKey()
    }
    // Land on the pinned model's provider; null (unknown pin) means all shut.
    var expanded by remember(currentProvider) { mutableStateOf(currentProvider) }

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
        LazyColumn(Modifier.heightIn(max = 340.dp)) {
            groups.forEach { (provider, list) ->
                val open = provider == expanded
                item(key = "provider:$provider") {
                    ProviderHeader(
                        label = PROVIDER_LABELS[provider] ?: provider,
                        count = list.size,
                        open = open,
                        holdsCurrent = provider == currentProvider,
                        onClick = { expanded = if (open) null else provider },
                    )
                }
                if (open) {
                    items(list, key = { it.id }) { m ->
                        ModelRow(model = m, active = m.id == current, onClick = { onPick(m.id) })
                    }
                }
            }
        }
    }
}

/** One collapsed provider group — label, model count, open/shut chevron. */
@Composable
private fun ProviderHeader(
    label: String,
    count: Int,
    open: Boolean,
    holdsCurrent: Boolean,
    onClick: () -> Unit,
) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .background(if (open) palette.accent.copy(alpha = 0.10f) else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        // Shut groups still say where the pin lives.
        Box(
            Modifier
                .size(5.dp)
                .clip(CircleShape)
                .background(if (holdsCurrent) palette.accentBright else Color.Transparent),
        )
        HbText(
            label,
            style = HbType.headCyan.copy(fontSize = 11.sp, letterSpacing = 0.16.em),
            color = if (open || holdsCurrent) palette.accentBright else palette.iconBright,
            caps = true,
            maxLines = 1,
            modifier = Modifier.weight(1f),
        )
        HbText(
            count.toString(),
            style = HbType.readout.copy(fontSize = 10.sp),
            color = palette.textFaint,
        )
        if (open) {
            HbGlyphs.ChevronUp(palette.accentBright, size = 9.dp)
        } else {
            HbGlyphs.ChevronDown(palette.iconDim, size = 9.dp)
        }
    }
}

/** One model inside an open provider group. */
@Composable
private fun ModelRow(model: ModelInfo, active: Boolean, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .background(if (active) palette.accent.copy(alpha = 0.12f) else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(start = 25.dp, end = 12.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Column(Modifier.weight(1f)) {
            HbText(
                shortModelName(model.name.ifEmpty { model.id }),
                style = HbType.headerBar.copy(
                    fontSize = 11.sp,
                    fontWeight = if (active) FontWeight.Bold else FontWeight.SemiBold,
                    letterSpacing = 0.1.em,
                ),
                color = if (active) Color.White else palette.textDim,
                caps = true,
                maxLines = 1,
            )
            if (model.description.isNotBlank()) {
                HbText(
                    model.description,
                    style = HbType.readout.copy(fontSize = 9.5.sp),
                    color = palette.iconDim,
                    maxLines = 2,
                )
            }
        }
        if (active) HbGlyphs.Check(palette.accentBright, size = 11.dp)
    }
}
