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
import androidx.compose.foundation.layout.Spacer
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.core.net.toUri
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.glass.toShape
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

/*
 * The settings pane's vocabulary — mirror of the desktop's settingsUI.tsx.
 *
 * Every tab is built from three shapes and nothing else: a labelled FIELD, a
 * ROW that states a setting in words with its control on the right, and a
 * SECTION heading. Drawing from one set is what makes the tabs read as one
 * screen rather than several separately-styled forms.
 *
 * Surfaces inside the pane are FLAT tinted rects, not the glass material: a
 * settings row is a region of the page, and stacking glass slabs on a glass
 * page turns a form into a pile of cards.
 */

/** Fill/rim the pane's rows and fields share. */
private val ROW_FILL = Color.White.copy(alpha = 0.03f)
private val ROW_RIM = Color.White.copy(alpha = 0.07f)
private val FIELD_RIM = Color.White.copy(alpha = 0.09f)

/** Section heading — sentence case, with the rule ABOVE it. */
@Composable
internal fun SectionHeader(title: String, first: Boolean = false) {
    val palette = LocalHbPalette.current
    Column(Modifier.fillMaxWidth().padding(top = if (first) 0.dp else 22.dp, bottom = 12.dp)) {
        if (!first) {
            Box(Modifier.height(1.dp).fillMaxWidth().background(ROW_RIM))
            Spacer(Modifier.height(22.dp))
        }
        HbText(title, style = HbType.headerBar.copy(fontSize = 17.sp), color = palette.text)
    }
}

@Composable
internal fun Panel(content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier.fillMaxWidth().hbGlass(shape = HbGlassShape.Ctl).padding(horizontal = 12.dp, vertical = 12.dp),
        content = content,
    )
}

@Composable
internal fun FieldLabel(text: String) {
    HbText(
        text,
        style = HbType.read.copy(fontSize = 13.5.sp),
        color = LocalHbPalette.current.textDim,
        modifier = Modifier.padding(bottom = 8.dp),
    )
}

/** A muted explanatory line under a control (the web's help paragraphs). */
@Composable
internal fun Hint(text: String) {
    HbText(
        text,
        style = HbType.read.copy(fontSize = 13.sp, lineHeight = 1.5.em),
        color = LocalHbPalette.current.textFaint,
    )
}

/**
 * A setting stated in words, with its control on the right — the pane's most
 * common shape. [ToggleRow] is this with a switch already in the slot.
 */
@Composable
internal fun SettingsRow(
    title: String,
    desc: String = "",
    trailing: @Composable () -> Unit,
) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .clip(HbGlassShape.Tile.toShape())
            .background(ROW_FILL)
            .border(1.dp, ROW_RIM, HbGlassShape.Tile.toShape())
            .padding(horizontal = 18.dp, vertical = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Column(Modifier.weight(1f)) {
            HbText(title, style = HbType.read.copy(fontSize = 15.sp), color = palette.text)
            if (desc.isNotEmpty()) {
                HbText(desc, style = HbType.read.copy(fontSize = 13.sp), color = palette.textFaint)
            }
        }
        trailing()
    }
}

/** A connected-service line: mark tile, name, state on the right. */
@Composable
internal fun ServiceRow(
    name: String,
    desc: String = "",
    tint: Color? = null,
    icon: @Composable () -> Unit,
    trailing: @Composable () -> Unit,
) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .clip(HbGlassShape.Tile.toShape())
            .background(ROW_FILL)
            .border(1.dp, ROW_RIM, HbGlassShape.Tile.toShape())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Box(
            Modifier
                .size(32.dp)
                .clip(HbGlassShape.Tile.toShape())
                .background((tint ?: palette.text).copy(alpha = if (tint != null) 0.18f else 0.04f))
                .border(1.dp, (tint ?: Color.White).copy(alpha = if (tint != null) 0.32f else 0.09f), HbGlassShape.Tile.toShape()),
            contentAlignment = Alignment.Center,
        ) { icon() }
        Column(Modifier.weight(1f)) {
            HbText(name, style = HbType.read.copy(fontSize = 15.sp), color = palette.text)
            if (desc.isNotEmpty()) {
                HbText(desc, style = HbType.read.copy(fontSize = 13.sp), color = palette.textFaint)
            }
        }
        trailing()
    }
}

/** "Connected", with the dot the whole deck uses for a live thing. */
@Composable
internal fun LiveDot(label: String = "Connected") {
    val palette = LocalHbPalette.current
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Box(Modifier.size(6.dp).background(palette.green, CircleShape))
        HbText(label, style = HbType.read.copy(fontSize = 13.5.sp), color = palette.green)
    }
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
    visualTransformation: VisualTransformation = VisualTransformation.None,
) {
    val palette = LocalHbPalette.current
    val base = if (mono) HbType.code.copy(fontSize = 13.sp) else HbType.read.copy(fontSize = 15.sp)
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        singleLine = singleLine,
        visualTransformation = visualTransformation,
        textStyle = base.merge(TextStyle(color = palette.text)),
        cursorBrush = SolidColor(palette.accentBright),
        modifier = Modifier.fillMaxWidth(),
        decorationBox = { inner ->
            Box(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = minHeight)
                    .clip(HbGlassShape.Tile.toShape())
                    .background(ROW_FILL)
                    .border(
                        1.dp,
                        if (dirty) palette.accent.copy(alpha = 0.45f) else FIELD_RIM,
                        HbGlassShape.Tile.toShape(),
                    )
                    .padding(horizontal = 16.dp, vertical = 12.dp),
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
    SettingsRow(title = label, desc = subtitle) {
        HbToggle(checked = checked, enabled = enabled, onToggle = onToggle)
    }
}

/**
 * The deck's switch — a 46×26 track with a white knob, accent when on.
 *
 * It was a 40×23 track tinted GREEN. Green means "nominal" everywhere else in
 * the app, which made every enabled setting read as a health indicator; the
 * brand colour is what says "this is on".
 */
@Composable
internal fun HbToggle(
    checked: Boolean,
    enabled: Boolean = true,
    color: Color = LocalHbPalette.current.accent,
    onToggle: (Boolean) -> Unit,
) {
    val palette = LocalHbPalette.current
    val knobX by animateDpAsState(if (checked) 22.dp else 2.dp, label = "toggleKnob")
    Box(
        Modifier
            .size(width = 46.dp, height = 26.dp)
            .background(if (checked) color else Color.White.copy(alpha = 0.06f), CircleShape)
            .then(
                if (checked) Modifier
                else Modifier.border(1.dp, Color.White.copy(alpha = 0.12f), CircleShape),
            )
            .then(if (enabled) Modifier.clickable { onToggle(!checked) } else Modifier),
    ) {
        Box(
            Modifier
                .offset(x = knobX)
                .align(Alignment.CenterStart)
                .size(22.dp)
                .background(if (checked) Color.White else palette.textFaint, CircleShape),
        )
    }
}

/** Small status dot — green when ok, amber/red otherwise. */
@Composable
internal fun StatusDot(ok: Boolean, warnColor: Color = LocalHbPalette.current.amber) {
    val palette = LocalHbPalette.current
    Box(Modifier.size(8.dp).background(if (ok) palette.green else warnColor, CircleShape))
}

/**
 * A pill action (Connect / Import / Save …) — the pane's only button shape.
 * It was a caps label on a tinted tile; the deck's actions are sentence-case
 * pills, and a settings form is the last place that needs shouting.
 */
@Composable
internal fun SettingsButton(
    label: String,
    onClick: () -> Unit,
    enabled: Boolean = true,
    tint: Color? = null,
) {
    val palette = LocalHbPalette.current
    val accented = tint != null
    val c = tint ?: palette.text
    Box(
        Modifier
            .heightIn(min = 32.dp)
            .clip(CircleShape)
            .background(if (accented) c.copy(alpha = 0.12f) else ROW_FILL)
            .border(1.dp, if (accented) c.copy(alpha = 0.32f) else Color.White.copy(alpha = 0.10f), CircleShape)
            .then(if (enabled) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 16.dp, vertical = 7.dp),
        contentAlignment = Alignment.Center,
    ) {
        HbText(
            label,
            style = HbType.read.copy(fontSize = 13.5.sp),
            color = if (enabled) c else palette.textFaint,
        )
    }
}
