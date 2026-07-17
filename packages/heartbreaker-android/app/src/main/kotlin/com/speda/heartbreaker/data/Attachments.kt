package com.speda.heartbreaker.data

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import kotlin.math.max
import kotlin.math.roundToInt

/** A base64 image block ready for the API (lib/types ImageBlock). */
data class ImageBlock(val mediaType: String, val data: String) {
    /** The `data:` URL the user bubble displays, exactly as the web stores it. */
    fun asDataUrl(): String = "data:$mediaType;base64,$data"
}

/** A non-image upload; the backend extracts its text (lib/types DocBlock). */
data class DocBlock(val name: String, val mediaType: String, val data: String, val size: Long)

/**
 * Turning picked content into API blocks — the Android side of fileToImageBlock /
 * fileToDocBlock in lib/api.ts.
 *
 * Images are downscaled to ≤1568px on the long edge (Anthropic's recommended max:
 * keeps well under the 5MB limit and cuts token cost), then re-encoded as PNG if
 * the source was PNG, else JPEG q0.9 — same rule as the web.
 */
object Attachments {

    private const val MAX_EDGE = 1568
    private const val JPEG_QUALITY = 90

    suspend fun imageBlock(context: Context, uri: Uri): ImageBlock? = withContext(Dispatchers.IO) {
        runCatching {
            val resolver = context.contentResolver
            val sourceType = resolver.getType(uri).orEmpty()
            val wantPng = sourceType == "image/png"

            // Measure first so a huge photo is never fully decoded into memory.
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
            val longest = max(bounds.outWidth, bounds.outHeight)
            if (longest <= 0) return@runCatching null

            var sample = 1
            while (longest / (sample * 2) >= MAX_EDGE) sample *= 2

            val decoded = resolver.openInputStream(uri)?.use {
                BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inSampleSize = sample })
            } ?: return@runCatching null

            val edge = max(decoded.width, decoded.height)
            val scaled = if (edge > MAX_EDGE) {
                val k = MAX_EDGE.toFloat() / edge
                Bitmap.createScaledBitmap(decoded, (decoded.width * k).roundToInt(), (decoded.height * k).roundToInt(), true)
            } else {
                decoded
            }

            val out = ByteArrayOutputStream()
            scaled.compress(
                if (wantPng) Bitmap.CompressFormat.PNG else Bitmap.CompressFormat.JPEG,
                JPEG_QUALITY,
                out,
            )
            if (scaled !== decoded) decoded.recycle()

            ImageBlock(
                mediaType = if (wantPng) "image/png" else "image/jpeg",
                data = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP),
            )
        }.getOrNull()
    }

    suspend fun docBlock(context: Context, uri: Uri): DocBlock? = withContext(Dispatchers.IO) {
        runCatching {
            val resolver = context.contentResolver
            val (name, size) = queryNameAndSize(context, uri)
            val bytes = resolver.openInputStream(uri)?.use { it.readBytes() } ?: return@runCatching null
            DocBlock(
                name = name,
                mediaType = resolver.getType(uri) ?: "application/octet-stream",
                data = Base64.encodeToString(bytes, Base64.NO_WRAP),
                size = if (size > 0) size else bytes.size.toLong(),
            )
        }.getOrNull()
    }

    /** True for anything we should send as an image block rather than a document. */
    fun isImage(context: Context, uri: Uri): Boolean =
        context.contentResolver.getType(uri)?.startsWith("image/") == true

    fun displayName(context: Context, uri: Uri): String = queryNameAndSize(context, uri).first

    private fun queryNameAndSize(context: Context, uri: Uri): Pair<String, Long> {
        var name = uri.lastPathSegment?.substringAfterLast('/') ?: "file"
        var size = 0L
        runCatching {
            context.contentResolver.query(uri, null, null, null, null)?.use { c ->
                if (c.moveToFirst()) {
                    c.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }
                        ?.let { if (!c.isNull(it)) name = c.getString(it) }
                    c.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 }
                        ?.let { if (!c.isNull(it)) size = c.getLong(it) }
                }
            }
        }
        return name to size
    }
}
