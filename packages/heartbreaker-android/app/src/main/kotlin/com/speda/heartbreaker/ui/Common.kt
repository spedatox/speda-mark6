package com.speda.heartbreaker.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.HbPalette
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType

/** On-brand text — foundation BasicText so no Material theme is pulled in. */
@Composable
fun HbText(
    text: String,
    modifier: Modifier = Modifier,
    style: TextStyle = HbType.read,
    color: Color = LocalHbPalette.current.text,
    caps: Boolean = false,
    maxLines: Int = Int.MAX_VALUE,
) {
    BasicText(
        text = if (caps) text.uppercase() else text,
        modifier = modifier,
        style = style.merge(TextStyle(color = color)),
        maxLines = maxLines,
        overflow = TextOverflow.Ellipsis,
    )
}

/** Glass pill/slab button on the single material (`.hb-btn`). */
@Composable
fun HbGlassButton(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    state: HbGlassState = HbGlassState.Default,
    shape: HbGlassShape = HbGlassShape.R12,
    contentColor: Color = LocalHbPalette.current.iconBright,
    padding: PaddingValues = PaddingValues(horizontal = 14.dp, vertical = 8.dp),
) {
    Box(
        modifier = modifier
            .hbGlass(shape = shape, state = state)
            .clickable(onClick = onClick)
            .padding(padding),
        contentAlignment = Alignment.Center,
    ) {
        HbText(label, style = HbType.label, color = contentColor, caps = true)
    }
}

/** Convenience accessor mirroring the CSS `currentColor` amber usage. */
val HbPalette.amberText: Color get() = amberBright
