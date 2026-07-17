package com.speda.heartbreaker.ui.prose

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbFonts
import com.speda.heartbreaker.designsystem.type.HbType
import kotlinx.coroutines.delay
import java.util.Locale

/**
 * Glass code block — a port of CodeBlock.tsx: a 12dp glass slab with a frosted
 * accent tag bar (language + ".EXT document") and a copy control, over the
 * `--bg-code` gutter in JetBrains Mono.
 *
 * Syntax highlighting is not wired yet; the web colours via Prism/vscDarkPlus and
 * the Kotlin equivalent (dev.snipme:highlights) is a follow-up. Everything else —
 * chrome, metrics, copy behaviour — is in place.
 */
@Composable
fun CodeBlockView(language: String, code: String, modifier: Modifier = Modifier) {
    val palette = LocalHbPalette.current
    val clipboard = LocalClipboardManager.current
    var copied by remember { mutableStateOf(false) }
    LaunchedEffect(copied) {
        if (copied) { delay(2000); copied = false }
    }
    val lang = language.ifBlank { "text" }

    Column(
        modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp)
            .clip(RoundedCornerShape(12.dp))
            .border(1.dp, palette.edge, RoundedCornerShape(12.dp)),
    ) {
        // Header — frosted accent glass tag bar
        Row(
            Modifier
                .fillMaxWidth()
                .background(palette.accent.copy(alpha = 0.10f))
                .padding(horizontal = 12.dp, vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                BasicText(
                    text = AnnotatedString(lang.uppercase(Locale.ENGLISH)),
                    style = HbType.headerBar.copy(
                        fontSize = 10.5.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.16.em,
                        color = androidx.compose.ui.graphics.Color(0xFFCFE7EE),
                    ),
                )
                BasicText(
                    text = AnnotatedString(".${lang.take(3).uppercase(Locale.ENGLISH)} document"),
                    style = HbType.readout.copy(
                        fontSize = 9.sp, letterSpacing = 0.08.em, color = palette.textFaint,
                    ),
                )
            }
            Row(
                Modifier.clickable {
                    clipboard.setText(AnnotatedString(code))
                    copied = true
                }.padding(horizontal = 4.dp, vertical = 2.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                BasicText(
                    text = AnnotatedString(if (copied) "COPIED" else "COPY"),
                    style = HbType.headerBar.copy(
                        fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.14.em,
                        color = if (copied) palette.green else palette.textDim,
                    ),
                )
            }
        }

        // Code — scrolls horizontally rather than wrapping.
        Box(
            Modifier
                .fillMaxWidth()
                .background(palette.bgCode)
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 14.dp),
        ) {
            BasicText(
                text = AnnotatedString(code),
                style = TextStyle(
                    fontFamily = HbFonts.Mono,
                    fontSize = 13.sp,
                    lineHeight = 1.6.em,
                    color = palette.text,
                ),
                softWrap = false,
            )
        }
    }
}
