package com.speda.heartbreaker.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.ModelInfo
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.ui.HbText
import java.util.Locale

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
    onSend: (String) -> Unit,
    onStop: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current
    var text by remember { mutableStateOf("") }
    var pickerOpen by remember { mutableStateOf(false) }
    val canSend = text.isNotBlank()

    Column(modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 6.dp)) {
        Box {
            Column(
                Modifier
                    .fillMaxWidth()
                    .hbGlass(shape = HbGlassShape.R14)
                    .padding(horizontal = 12.dp, vertical = 10.dp),
            ) {
                BasicTextField(
                    value = text,
                    onValueChange = { text = it },
                    textStyle = HbType.read.merge(TextStyle(color = palette.text)),
                    cursorBrush = SolidColor(palette.accentBright),
                    maxLines = 6,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp),
                    decorationBox = { inner ->
                        if (text.isEmpty()) {
                            HbText(
                                "How can I help you today?",
                                style = HbType.read.copy(fontSize = 16.sp),
                                color = palette.textFaint,
                            )
                        }
                        inner()
                    },
                )

                Spacer(Modifier.size(8.dp))

                Row(verticalAlignment = Alignment.CenterVertically) {
                    // "+" overflow — attach / budget / voice arrive with M3's tail.
                    Box(
                        Modifier
                            .size(30.dp)
                            .clip(CircleShape)
                            .hbGlass(shape = HbGlassShape.Pill)
                            .clickable { /* overflow menu — next pass */ },
                        contentAlignment = Alignment.Center,
                    ) { HbGlyphs.Plus(palette.iconBright, size = 14.dp) }

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
                            .clickable {
                                if (isStreaming) onStop()
                                else if (canSend) { onSend(text.trim()); text = "" }
                            },
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
        }

        // Status strip
        HbText(
            "$agentName can make mistakes",
            style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.04.em),
            color = palette.textFaint,
            modifier = Modifier.padding(top = 6.dp, start = 4.dp),
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
