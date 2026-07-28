package com.speda.heartbreaker.data

import android.content.ContentValues
import android.content.Context
import android.os.Environment
import android.provider.MediaStore
import android.webkit.MimeTypeMap
import com.speda.heartbreaker.domain.AppConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * File delivery — fetches a produced file with the auth header and files it under
 * `Documents/Speda Mark VI/` so the owner's downloads folder stays uncluttered
 * and there's one obvious place everything Igor makes lands.
 *
 * The web does `fetch` → blob → an `<a download>` click. The Android equivalent is
 * MediaStore: we insert into the Files collection with a RELATIVE_PATH into
 * Documents (the Downloads collection only accepts paths under Download/). On
 * API 29+ an app may write to Documents without any storage permission, which is
 * why this declares none (we're minSdk 31).
 *
 * MediaStore de-duplicates display names itself, so re-downloading the same file
 * yields "report (1).pdf" rather than clobbering the original.
 */
class Downloader(private val context: Context, private val client: OkHttpClient) {

    /** Where everything Igor produces is filed. */
    private val relativePath = "${Environment.DIRECTORY_DOCUMENTS}/$FOLDER"

    /** @return the folder-relative location it was saved to, or null on failure. */
    suspend fun download(config: AppConfig, url: String, filename: String): String? = withContext(Dispatchers.IO) {
        runCatching {
            val request = Request.Builder()
                .url("${config.apiBase}$url")
                .header("X-API-Key", config.apiKey)
                .get()
                .build()

            client.newCall(request).execute().use { res ->
                if (!res.isSuccessful) return@withContext null

                val mime = res.body?.contentType()?.let { "${it.type}/${it.subtype}" } ?: guessMime(filename)
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, filename)
                    mime?.let { put(MediaStore.MediaColumns.MIME_TYPE, it) }
                    put(MediaStore.MediaColumns.RELATIVE_PATH, relativePath)
                    put(MediaStore.MediaColumns.IS_PENDING, 1)
                }

                val resolver = context.contentResolver
                val collection = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
                val uri = resolver.insert(collection, values) ?: return@withContext null

                resolver.openOutputStream(uri)?.use { out ->
                    res.body?.byteStream()?.copyTo(out) ?: return@withContext null
                } ?: return@withContext null

                values.clear()
                values.put(MediaStore.MediaColumns.IS_PENDING, 0)
                resolver.update(uri, values, null, null)

                "$FOLDER/$filename"
            }
        }.getOrNull()
    }

    /** MediaStore wants a MIME type; fall back to the extension when the server
     *  doesn't send one (Content-Type is often octet-stream for generated files). */
    private fun guessMime(filename: String): String? {
        val ext = filename.substringAfterLast('.', "").lowercase()
        if (ext.isEmpty()) return null
        return MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext)
    }

    companion object {
        const val FOLDER = "Speda Mark VI"
    }
}
