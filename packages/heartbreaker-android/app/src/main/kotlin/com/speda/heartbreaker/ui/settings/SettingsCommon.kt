package com.speda.heartbreaker.ui.settings

import android.content.Context
import android.content.Intent
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.core.net.toUri
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.ui.HbText

/** Open a URL in the system browser (OAuth consent, Telegram deep link). */
internal fun openUrl(context: Context, url: String) {
    runCatching {
        context.startActivity(
            Intent(Intent.ACTION_VIEW, url.toUri()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}

@Composable
internal fun SectionHeader(title: String) {
    val palette = LocalHbPalette.current
    Row(
        Modifier.fillMaxWidth().padding(top = 18.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        HbText(title, style = HbType.headCyan, color = palette.accent, caps = true)
        Box(
            Modifier.height(1.dp).fillMaxWidth().background(
                Brush.horizontalGradient(listOf(palette.accent.copy(alpha = 0.28f), palette.accent.copy(alpha = 0.03f))),
            ),
        )
    }
}

@Composable
internal fun Panel(content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier.fillMaxWidth().hbGlass(shape = HbGlassShape.R12).padding(horizontal = 12.dp, vertical = 12.dp),
        content = content,
    )
}

@Composable
internal fun FieldLabel(text: String) {
    HbText(text, style = HbType.label, color = LocalHbPalette.current.textDim, caps = true, modifier = Modifier.padding(bottom = 6.dp))
}

/** A muted explanatory line under a control (the web's help paragraphs). */
@Composable
internal fun Hint(text: String) {
    HbText(text, style = HbType.readout.copy(fontSize = 11.sp, lineHeight = 1.5.em), color = LocalHbPalette.current.textFaint)
}

@Composable
internal fun GlassField(
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    singleLine: Boolean,
    minHeight: Dp = 44.dp,
    dirty: Boolean = false,
    mono: Boolean = false,
) {
    val palette = LocalHbPalette.current
    val base = if (mono) HbType.code.copy(fontSize = 13.sp) else HbType.read.copy(fontSize = 15.sp)
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        singleLine = singleLine,
        textStyle = base.merge(TextStyle(color = palette.text)),
        cursorBrush = SolidColor(palette.accentBright),
        modifier = Modifier.fillMaxWidth(),
        decorationBox = { inner ->
            Box(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = minHeight)
                    .hbGlass(shape = HbGlassShape.R9, state = if (dirty) HbGlassState.Active else HbGlassState.Default)
                    .padding(horizontal = 12.dp, vertical = 11.dp),
                contentAlignment = if (singleLine) Alignment.CenterStart else Alignment.TopStart,
            ) {
                if (value.isEmpty()) HbText(placeholder, style = base, color = palette.textFaint)
                inner()
            }
        },
    )
}

@Composable
internal fun ToggleRow(
    label: String,
    subtitle: String,
    checked: Boolean,
    enabled: Boolean,
    onToggle: (Boolean) -> Unit,
) {
    val palette = LocalHbPalette.current
    Row(
        Modifier.fillMaxWidth().padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(Modifier.weight(1f)) {
            HbText(label, style = HbType.read.copy(fontSize = 14.sp, fontWeight = FontWeight.Medium), color = palette.text)
            if (subtitle.isNotEmpty()) HbText(subtitle, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint)
        }
        HbToggle(checked = checked, enabled = enabled, onToggle = onToggle)
    }
}

/** Pill-track switch on the glass material. `color` tints the "on" state. */
@Composable
internal fun HbToggle(
    checked: Boolean,
    enabled: Boolean = true,
    color: Color = LocalHbPalette.current.green,
    onToggle: (Boolean) -> Unit,
) {
    val palette = LocalHbPalette.current
    val knobX by animateDpAsState(if (checked) 20.dp else 3.dp, label = "toggleKnob")
    Box(
        Modifier
            .size(width = 40.dp, height = 23.dp)
            .background(if (checked) color.copy(alpha = 0.28f) else palette.glassFill, CircleShape)
            .border(1.dp, if (checked) color.copy(alpha = 0.6f) else palette.edge, CircleShape)
            .then(if (enabled) Modifier.clickable { onToggle(!checked) } else Modifier),
    ) {
        Box(
            Modifier
                .offset(x = knobX)
                .align(Alignment.CenterStart)
                .size(17.dp)
                .background(if (checked) color else palette.iconDim, CircleShape),
        )
    }
}

/** Small status dot — green when ok, amber/red otherwise. */
@Composable
internal fun StatusDot(ok: Boolean, warnColor: Color = LocalHbPalette.current.amber) {
    val palette = LocalHbPalette.current
    Box(Modifier.size(8.dp).background(if (ok) palette.green else warnColor, CircleShape))
}

/** A glass action button (Connect / Import / Save …). */
@Composable
internal fun SettingsButton(
    label: String,
    onClick: () -> Unit,
    enabled: Boolean = true,
    tint: Color? = null,
) {
    val palette = LocalHbPalette.current
    val c = tint ?: palette.accentBright
    Box(
        Modifier
            .hbGlass(shape = HbGlassShape.R9, state = if (enabled) HbGlassState.Tint(c) else HbGlassState.Default)
            .then(if (enabled) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 14.dp, vertical = 9.dp),
        contentAlignment = Alignment.Center,
    ) {
        HbText(
            label,
            style = HbType.headerBar.copy(fontSize = 12.sp),
            color = if (enabled) c else palette.textFaint,
            caps = true,
        )
    }
}
