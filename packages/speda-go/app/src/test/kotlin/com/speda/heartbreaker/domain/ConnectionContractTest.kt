package com.speda.heartbreaker.domain

import com.speda.heartbreaker.data.ConnectionsResult
import com.speda.heartbreaker.data.CustomMcpResult
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test

class ConnectionContractTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun nativeConnectionStatusAcceptsUnavailableProviderLimit() {
        val result = json.decodeFromString<ConnectionsResult>(
            """{"servers":[{"server":"fixture","connected":false,"active":true,"tools":0,"tokens":0,"error":"Reconnect required"}],"active_tool_tokens":0,"itpm_limit":null,"lazy":true}"""
        )
        assertNull(result.itpmLimit)
        assertEquals("Reconnect required", result.servers.single().error)
        assertFalse(result.servers.single().connected)
        assertTrue(result.servers.single().active)
    }

    @Test
    fun maskedCustomCredentialsAndNativeGrantFieldsRemainReadable() {
        val result = json.decodeFromString<CustomMcpResult>(
            """{"servers":[{"name":"fixture","transport":"http","url":"https://example.invalid/mcp","headers":{"Authorization":"••••••••"},"allowed_agents":["speda"],"read_only_tools":[],"connected":false}],"reserved":["notion"]}"""
        )
        assertEquals("••••••••", result.servers.single().headers["Authorization"])
        assertEquals(listOf("notion"), result.reserved)
    }
}
