package com.speda.heartbreaker.ui.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.ui.HbGlassButton
import com.speda.heartbreaker.ui.HbText

/**
 * The message composer — M1 is text-only (auto-grow field + send/stop). The
 * attach/budget/model/voice affordances behind the "+" overflow land in M3.
 */
@Composable
fun Composer(
    isStreaming: Boolean,
    onSend: (String) -> Unit,
    modifier: Modifier = Modifier,
    onStop: () -> Unit,
) {
    val palette = LocalHbPalette.current
    var text by remember { mutableStateOf("") }

    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 10.dp, vertical = 8.dp),
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(
            Modifier
                .weight(1f)
                .hbGlass(shape = HbGlassShape.R12)
                .padding(horizontal = 12.dp, vertical = 10.dp),
        ) {
            BasicTextField(
                value = text,
                onValueChange = { text = it },
                textStyle = HbType.read.merge(TextStyle(color = palette.text)),
                cursorBrush = SolidColor(palette.accentBright),
                maxLines = 6,
                modifier = Modifier.fillMaxWidth().heightIn(min = 20.dp),
                decorationBox = { inner ->
                    if (text.isEmpty()) {
                        HbText("Message…", style = HbType.read, color = palette.textFaint)
                    }
                    inner()
                },
            )
        }

        if (isStreaming) {
            HbGlassButton(
                label = "Stop",
                onClick = onStop,
                state = HbGlassState.Amber,
                shape = HbGlassShape.Pill,
                contentColor = palette.amberBright,
            )
        } else {
            val canSend = text.isNotBlank()
            HbGlassButton(
                label = "Send",
                onClick = {
                    if (canSend) {
                        onSend(text.trim())
                        text = ""
                    }
                },
                state = if (canSend) HbGlassState.Active else HbGlassState.Default,
                shape = HbGlassShape.Pill,
                contentColor = if (canSend) palette.accentBright else palette.textFaint,
            )
        }
    }
}
