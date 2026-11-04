package com.speda.heartbreaker.domain

import com.speda.heartbreaker.data.IgorApi
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CloudProjectTransportTest {
    @Test
    fun publicationCarriesOwnerScopeAndExactReviewAndSurfacesConflicts() = runBlocking {
        val calls = mutableListOf<String>()
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            val request = chain.request()
            assertEquals("/projects/42/cloud/publish", request.url.encodedPath)
            assertEquals("optimus", request.url.queryParameter("agent_id"))
            assertEquals("test-key", request.header("X-API-Key"))
            assertEquals("POST", request.method)
            val bytes = Buffer()
            request.body!!.writeTo(bytes)
            calls.add(bytes.readUtf8())
            Response.Builder().request(request).protocol(Protocol.HTTP_1_1)
                .code(409).message("Conflict")
                .body("""{"detail":"Review a prepared environment before publishing"}"""
                    .toResponseBody("application/json".toMediaType()))
                .build()
        }.build()
        val api = IgorApi(client, client)
        val review = buildJsonObject {
            put("environment_id", "a".repeat(32))
            put("review_hash", "b".repeat(64))
        }
        val failure = runCatching {
            api.projectRequest(AppConfig("https://fixture.invalid", "test-key", "optimus"), "/42/cloud/publish", review)
        }.exceptionOrNull()
        assertEquals("Review a prepared environment before publishing", failure?.message)
        assertTrue(calls.single().contains("b".repeat(64)))
    }
}
