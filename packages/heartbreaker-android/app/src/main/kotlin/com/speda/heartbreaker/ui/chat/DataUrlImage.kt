package com.speda.heartbreaker.ui.chat

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap

/**
 * Decode a `data:<mime>;base64,…` URL — the form the web stores attached images
 * in, which the transcript cache therefore round-trips too. Remembered by value,
 * so a bubble decodes once rather than on every recomposition.
 */
@Composable
fun rememberDataUrlImage(dataUrl: String): ImageBitmap? = remember(dataUrl) {
    runCatching {
        val b64 = dataUrl.substringAfter("base64,", "")
        if (b64.isEmpty()) return@runCatching null
        val bytes = Base64.decode(b64, Base64.DEFAULT)
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
    }.getOrNull()
}
