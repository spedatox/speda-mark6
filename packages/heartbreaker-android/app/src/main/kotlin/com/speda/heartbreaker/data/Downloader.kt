package com.speda.heartbreaker.data

import android.content.ContentValues
import android.content.Context
import android.provider.MediaStore
import com.speda.heartbreaker.domain.AppConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * File delivery — fetches a produced file with the auth header and saves it to
 * the system Downloads collection.
 *
 * The web does `fetch` → blob → an <a download> click; the Android equivalent is
 * MediaStore. On API 29+ an app may insert into Downloads without any storage
 * permission, which is why this needs none (we're minSdk 31).
 */
class Downloader(private val context: Context, private val client: OkHttpClient) {

    /** @return the saved display name, or null on failure. */
    suspend fun download(config: AppConfig, url: String, filename: String): String? = withContext(Dispatchers.IO) {
        runCatching {
            val request = Request.Builder()
                .url("${config.apiBase}$url")
                .header("X-API-Key", config.apiKey)
                .get()
                .build()

            client.newCall(request).execute().use { res ->
                if (!res.isSuccessful) return@withContext null
                val resolver = context.contentResolver
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, filename)
                    res.body?.contentType()?.let { put(MediaStore.Downloads.MIME_TYPE, "${it.type}/${it.subtype}") }
                    put(MediaStore.Downloads.IS_PENDING, 1)
                }
                val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: return@withContext null
                resolver.openOutputStream(uri)?.use { out ->
                    res.body?.byteStream()?.copyTo(out) ?: return@withContext null
                }
                values.clear()
                values.put(MediaStore.Downloads.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
                filename
            }
        }.getOrNull()
    }
}
