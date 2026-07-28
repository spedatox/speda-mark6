package com.speda.heartbreaker.data

import com.speda.heartbreaker.domain.ChatMessage
import com.speda.heartbreaker.domain.FileMeta
import com.speda.heartbreaker.domain.Role
import com.speda.heartbreaker.domain.ToolBadge
import com.speda.heartbreaker.domain.UploadedFile
import kotlinx.collections.immutable.toPersistentList
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import java.util.UUID

/**
 * Lenient, hand-rolled mapping between JSON and [ChatMessage]. Used for BOTH the
 * server (`/sessions/{id}/messages`, whose exact field types vary — e.g. `id` may
 * arrive as a number) and the offline transcript cache (whose format we own).
 * Hand-rolled rather than @Serializable because tool `input` is arbitrary JSON
 * and the message lists are PersistentList.
 */
object MessageJson {

    fun parseArray(array: JsonArray): List<ChatMessage> =
        array.mapNotNull { (it as? JsonObject)?.let(::parseMessage) }

    fun parseMessage(o: JsonObject): ChatMessage {
        val role = when (o["role"].asStringOrNull()) {
            "user" -> Role.User
            else -> Role.Assistant
        }
        val tools = (o["tools"] as? JsonArray).orEmpty()
            .mapNotNull { (it as? JsonObject)?.let(::parseTool) }
            .toPersistentList()
        val files = (o["files"] as? JsonArray)?.mapNotNull { (it as? JsonObject)?.let(::parseFile) }
            ?.toPersistentList()
        val images = (o["images"] as? JsonArray)?.mapNotNull { it.asStringOrNull() }?.toPersistentList()
        val uploads = (o["uploads"] as? JsonArray)?.mapNotNull { u ->
            (u as? JsonObject)?.let { UploadedFile(it["name"].asStringOrNull().orEmpty(), it["size"].asLong()) }
        }?.toPersistentList()

        return ChatMessage(
            id = o["id"].asStringOrNull() ?: randomId(),
            role = role,
            content = o["content"].asStringOrNull().orEmpty(),
            tools = tools,
            isStreaming = false,
            isError = (o["isError"] as? JsonPrimitive)?.let { it.content == "true" } ?: false,
            errorNote = o["errorNote"].asStringOrNull(),
            images = images,
            files = files,
            uploads = uploads,
            status = null,
            sessionId = o["sessionId"].asIntOrNull(),
        )
    }

    /** Parse a single tool/file from an SSE `data` element (tool / file events). */
    fun toolFrom(e: JsonElement): ToolBadge? = (e as? JsonObject)?.let(::parseTool)
    fun fileFrom(e: JsonElement): FileMeta? = (e as? JsonObject)?.let(::parseFile)

    private fun parseTool(o: JsonObject): ToolBadge = ToolBadge(
        id = o["id"].asStringOrNull() ?: randomId(),
        name = o["name"].asStringOrNull().orEmpty(),
        input = o["input"]?.takeUnless { it is JsonNull },
        result = o["result"].asStringOrNull(),
        afterChars = o["afterChars"].asIntOrNull(),
    )

    private fun parseFile(o: JsonObject): FileMeta = FileMeta(
        name = o["name"].asStringOrNull().orEmpty(),
        title = o["title"].asStringOrNull() ?: o["name"].asStringOrNull().orEmpty(),
        kind = o["kind"].asStringOrNull().orEmpty(),
        size = o["size"].asLong(),
        url = o["url"].asStringOrNull().orEmpty(),
    )

    /** Encode a transcript for the offline cache (we own this shape). */
    fun encodeArray(messages: List<ChatMessage>): JsonArray = buildJsonArray {
        for (m in messages) add(encodeMessage(m))
    }

    private fun encodeMessage(m: ChatMessage): JsonObject = buildJsonObject {
        put("id", m.id)
        put("role", if (m.role == Role.User) "user" else "assistant")
        put("content", m.content)
        // Drop volatile live flags so a reload never rehydrates a spinning bubble.
        put("isError", m.isError)
        m.errorNote?.let { put("errorNote", it) }
        m.sessionId?.let { put("sessionId", it) }
        put("tools", buildJsonArray {
            for (t in m.tools) add(buildJsonObject {
                put("id", t.id)
                put("name", t.name)
                t.input?.let { put("input", it) }
                t.result?.let { put("result", it) }
                t.afterChars?.let { put("afterChars", it) }
            })
        })
        m.files?.let { files ->
            put("files", buildJsonArray {
                for (f in files) add(buildJsonObject {
                    put("name", f.name); put("title", f.title); put("kind", f.kind)
                    put("size", f.size); put("url", f.url)
                })
            })
        }
        m.images?.let { imgs -> put("images", buildJsonArray { for (s in imgs) add(s) }) }
    }

    // ── coercion helpers (server field types vary) ──────────────────────────
    private fun JsonElement?.asStringOrNull(): String? {
        val p = this as? JsonPrimitive ?: return null
        return if (p is JsonNull) null else p.content
    }

    private fun JsonElement?.asIntOrNull(): Int? = (this as? JsonPrimitive)?.intOrNull

    private fun JsonElement?.asLong(): Long = (this as? JsonPrimitive)?.longOrNull ?: 0L

    private fun JsonArray?.orEmpty(): JsonArray = this ?: EMPTY

    private val EMPTY = JsonArray(emptyList())

    private fun randomId(): String = UUID.randomUUID().toString().replace("-", "").take(8)
}
