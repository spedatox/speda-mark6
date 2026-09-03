// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.prose

import android.graphics.BitmapFactory
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import com.speda.heartbreaker.domain.isImageUrl

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

/**
 * Decode one board picture, or nothing.
 *
 * Nothing is the important half. A dead link, a host that refuses, a URL the
 * model invented rather than found — all of them leave a window with its fields
 * and no photo, which is the intended degradation. A broken-image placeholder
 * on a dossier is worse than a dossier without a picture.
 */
@Composable
fun rememberBoardImage(url: String): ImageBitmap? {
    val resolve = LocalBoardImageResolver.current
    var image by remember(url) { mutableStateOf<ImageBitmap?>(null) }
    LaunchedEffect(url) {
        if (!isImageUrl(url)) return@LaunchedEffect
        val bytes = runCatching { resolve(url) }.getOrNull() ?: return@LaunchedEffect
        image = runCatching {
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
        }.getOrNull()
    }
    return image
}
