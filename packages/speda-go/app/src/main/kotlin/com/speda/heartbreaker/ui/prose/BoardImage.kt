// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.prose

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.SourceImage
import com.speda.heartbreaker.domain.isImageUrl
import com.speda.heartbreaker.i18n.LocalStrings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Resolves a third-party image URL into bytes, by asking Igor to fetch it.
 * Provided at the app root; defaults to "cannot resolve", so a preview renders
 * the window without its picture instead of crashing — the same contract the
 * route and place resolvers use.
 *
 * The picture is NOT loaded from its origin, and that is the point rather than
 * an inconvenience. A client that fetched `https://target.example/photo.jpg`
 * directly would tell that server the owner's IP and the moment he looked; on a
 * board about a person, that is precisely what the board must not do. The fetch
 * happens on the server (igor `routers/media.py`), which is already making
 * requests of its own.
 */
val LocalBoardImageResolver = compositionLocalOf<suspend (String) -> ByteArray?> { { null } }

data class BoardImageState(val bitmap: ImageBitmap? = null, val finished: Boolean = false)

@Composable
fun rememberBoardImage(url: String): BoardImageState {
    val resolve = LocalBoardImageResolver.current
    var image by remember(url, resolve) { mutableStateOf(BoardImageState()) }
    LaunchedEffect(url, resolve) {
        val bytes = if (isImageUrl(url)) resolve(url) else null
        val bitmap = bytes?.let {
            withContext(Dispatchers.Default) {
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
            }
        }
        image = BoardImageState(bitmap, finished = true)
    }
    return image
}

@Composable
fun ResearchImage(image: SourceImage, showCaption: Boolean = false) {
    val palette = LocalHbPalette.current
    val strings = LocalStrings.current.proseKind
    val state = rememberBoardImage(image.url)
    val bitmap = state.bitmap
    val captionStyle = HbType.read.copy(fontSize = 12.sp, color = palette.textDim)
    val shape = RoundedCornerShape(6.dp)

    Column(Modifier.padding(vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
        if (bitmap != null) {
            Image(
                bitmap = bitmap,
                contentDescription = image.caption.ifBlank { strings.image },
                contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxWidth().heightIn(max = 360.dp)
                    .aspectRatio(bitmap.width.toFloat() / bitmap.height)
                    .clip(shape).border(1.dp, palette.line, shape),
            )
        } else {
            val label = if (state.finished) strings.imageUnavailable else strings.imageLoading
            val description = if (state.finished && !showCaption && image.caption.isNotBlank()) {
                "$label · ${image.caption}"
            } else label
            BasicText(description, style = captionStyle)
        }
        if (showCaption && image.caption.isNotBlank()) BasicText(image.caption, style = captionStyle)
        image.source?.let { source ->
            val style = SpanStyle(color = palette.accentBright, textDecoration = TextDecoration.Underline)
            BasicText(buildAnnotatedString {
                withLink(LinkAnnotation.Url(source, TextLinkStyles(style = style))) { append(strings.openImageSource) }
            }, style = captionStyle)
        }
    }
}
