package com.speda.heartbreaker.domain

import com.speda.heartbreaker.data.*
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.OkHttpClient
import org.junit.Assert.*
import org.junit.Test
import java.net.ServerSocket
import java.net.InetAddress
import java.util.concurrent.TimeUnit
import java.util.concurrent.CopyOnWriteArrayList

class ClientTransportTest {
    private data class Received(val path: String, val key: String?, val format: String?, val method: String, val body: String)
    private fun server(response: (Received) -> Pair<Int, String>, block: (IgorApi, AppConfig, List<Received>) -> Unit) {
        val received = CopyOnWriteArrayList<Received>()
        val server = ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"))
        val thread = Thread {
            while (!server.isClosed) {
                try {
                    server.accept().use { socket ->
                        socket.soTimeout = 5000
                        val input = socket.getInputStream().buffered()
                        val header = StringBuilder()
                        while (!header.endsWith("\r\n\r\n")) {
                            val byte = input.read(); if (byte < 0) return@use
                            header.append(byte.toChar())
                        }
                        val lines = header.toString().split("\r\n")
                        val start = lines.first().split(' ')
                        val headers = lines.drop(1).filter { it.contains(':') }.associate {
                            it.substringBefore(':').lowercase() to it.substringAfter(':').trim()
                        }
                        val size = headers["content-length"]?.toInt() ?: 0
                        val bytes = ByteArray(size)
                        var offset = 0
                        while (offset < size) { val count = input.read(bytes, offset, size - offset); if (count < 0) return@use; offset += count }
                        val request = Received(start[1], headers["x-api-key"], headers["x-chat-format"], start[0], bytes.decodeToString())
                        received.add(request)
                        val (code, body) = response(request)
                        val payload = body.toByteArray()
                        val output = socket.getOutputStream()
                        output.write("HTTP/1.1 $code Result\r\nContent-Length: ${payload.size}\r\nConnection: close\r\n\r\n".toByteArray())
                        // Split UTF-8 and SSE lines across writes, as a mobile network can.
                        payload.forEach { output.write(byteArrayOf(it)); output.flush() }
                    }
                } catch (error: java.io.IOException) { if (!server.isClosed) throw error }
            }
        }.apply { isDaemon = true; start() }
        val client = OkHttpClient.Builder().callTimeout(5, TimeUnit.SECONDS).build()
        try { block(IgorApi(client, client), AppConfig("http://127.0.0.1:${server.localPort}", "test-owner-key", "speda"), received) }
        finally { server.close(); thread.join(1000); client.connectionPool.evictAll(); client.dispatcher.executorService.shutdownNow() }
    }

    @Test fun chat_attach_and_history_negotiate_one_format_and_send_target_reply_and_canvas() {
        server({ request -> 200 to if (request.path.contains("messages")) "[]" else
            "data: {\"type\":\"chunk\",\"data\":\"Merhaba 👋\",\"session_id\":1}\n\ndata: {\"type\":\"done\",\"session_id\":1}\n\n" }) { api, config, requests ->
            runBlocking {
                val frames = api.streamChat("Explain", 1, config, IgorApi.StreamOpts(requestId = "request", addressedAgentId = "ultron", replyTo = 9,
                    canvas = true, canvasMaxPanels = 8, clientContext = ClientContext("android", "phone", "17", "test", "en", voice = true))).toList()
                assertEquals("Merhaba 👋", frames.first().data.jsonPrimitive.content)
                api.attachStream(config, "request").toList()
                api.fetchMessages(config, 1)
            }
            assertEquals(3, requests.size)
            assertTrue(requests.all { it.key == "test-owner-key" && it.format == "room-v1" })
            val body = Json.parseToJsonElement(requests.first().body).jsonObject
            assertEquals("ultron", body["addressed_agent_id"]!!.jsonPrimitive.content)
            assertEquals(9, body["reply_to"]!!.jsonPrimitive.int)
            val context = body["client_context"]!!.jsonObject
            assertTrue(context["voice"]!!.jsonPrimitive.boolean)
            assertTrue(context["canvas"]!!.jsonPrimitive.boolean)
            assertTrue(context["native_artifacts"]!!.jsonPrimitive.boolean)
            assertEquals(8, context["canvas_max_panels"]!!.jsonPrimitive.int)
        }
    }

    @Test fun steering_transmits_stable_input_identity_and_expected_step() {
        server({ 200 to "{\"steered\":true}" }) { api, config, requests ->
            runBlocking { assertTrue(api.steerRun(config, "request", "Use November", "same-input", "original-step")) }
            val body = Json.parseToJsonElement(requests.single().body).jsonObject
            assertEquals("same-input", body["message_id"]!!.jsonPrimitive.content)
            assertEquals("original-step", body["expected_step_id"]!!.jsonPrimitive.content)
            assertEquals("POST", requests.single().method)
        }
    }

    @Test fun chat_upload_rejection_displays_only_the_server_message() {
        server({ 422 to """{"detail":[{"msg":"Attachment exceeds 10 MiB","input":"private attachment"}]}""" }) { api, config, _ ->
            val error = assertThrows(ApiFailure::class.java) { runBlocking {
                api.streamChat("Read the slides", null, config).toList()
            } }
            assertEquals("Attachment exceeds 10 MiB", error.message)
            assertFalse(error.retryable)
        }
    }

    @Test fun membership_conflict_is_never_reported_as_success_or_automatically_replayed() {
        server({ 409 to "{\"detail\":\"stale version\"}" }) { api, config, requests ->
            assertThrows(ApiFailure::class.java) { runBlocking {
                api.changeRoomMember(config, RoomInfo("room", lead = "speda", members = listOf("speda"), version = 3, sequence = 5), "invite", "ultron")
            } }
            assertEquals(1, requests.size)
            assertEquals(3, Json.parseToJsonElement(requests.single().body).jsonObject["expected_version"]!!.jsonPrimitive.int)
        }
    }

    @Test fun worker_recovery_uses_authenticated_reads_and_server_cursor() {
        server({ request -> 200 to if (request.path.contains("snapshot")) "{\"session_id\":1,\"sequence\":17,\"omitted\":0,\"cards\":[]}" else
            "data: {\"type\":\"subagent\",\"data\":{\"sequence\":18}}\n\n" }) { api, config, requests ->
            runBlocking {
                val snapshot = api.snapshotSessionWorkers(config, 1)
                api.attachSessionWorkers(config, 1, snapshot.sequence).toList()
            }
            assertEquals(listOf("/legion/sessions/1/snapshot", "/legion/sessions/1/attach?after=17"), requests.map { it.path })
            assertTrue(requests.all { it.method == "GET" && it.key == "test-owner-key" })
        }
    }
}
