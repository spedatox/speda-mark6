package com.speda.heartbreaker.domain

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Tool → natural-language status and one-line summaries. Literal port of the
 * TOOL_STATUS map, statusLabel and shortPath in Message.tsx.
 */
object ToolStatus {

    /** Raw tool name → present-progressive status. */
    val TOOL_STATUS: Map<String, String> = mapOf(
        "read_skill" to "Reviewing capabilities",
        "generate_document" to "Preparing the document",
        "system_info" to "Checking system status",
        "text_to_speech" to "Generating audio",
        "speech_to_text" to "Transcribing audio",
        "send_push_notification" to "Sending a notification",
        "web_search" to "Searching the web",
        "WebSearch" to "Searching the web",
        "web_fetch" to "Reading the page",
        "WebFetch" to "Reading the page",
        "Task" to "Deploying the Legion",
        "legion_status" to "Checking on the Legion",
        "run_command" to "Running a command",
        "read_file" to "Reading a file",
        "write_file" to "Writing a file",
        "edit_file" to "Editing a file",
        "graph_query" to "Searching the codebase graph",
        "graph_path" to "Tracing the codebase graph",
        "graph_overview" to "Mapping the codebase graph",
    )

    fun statusLabel(toolName: String): String =
        TOOL_STATUS[toolName] ?: "Using ${toolName.replace('_', ' ')}"

    /** Keep the last two path segments so long paths stay readable. */
    fun shortPath(p: String): String {
        val parts = p.split('/', '\\').filter { it.isNotEmpty() }
        return if (parts.size <= 2) p else "…/" + parts.takeLast(2).joinToString("/")
    }
}

/** Read a string field from a tool's JSON input, or null. */
fun ToolBadge.str(key: String): String? {
    val obj = input as? JsonObject ?: return null
    val prim = obj[key] as? JsonPrimitive ?: return null
    return if (prim.isString) prim.content else null
}

/** All string fields of the tool input, in order (for the generic detail view). */
fun ToolBadge.inputRows(): List<Pair<String, String>> {
    val obj = (input as? JsonObject) ?: return emptyList()
    val rows = ArrayList<Pair<String, String>>()
    for ((k, v) in obj) {
        val prim = v as? JsonPrimitive
        val value = if (prim != null && prim.isString) prim.content else v.toString()
        if (value.isEmpty() || value == "null") continue
        rows.add(k to if (value.length > 400) value.take(400) + "…" else value)
    }
    return rows
}

/** String rendering of a raw tool input value — content for a string primitive, else its JSON form. */
private fun jsonValueString(v: JsonElement): String {
    val prim = v as? JsonPrimitive
    return if (prim != null && prim.isString) prim.content else v.toString()
}

// The arg a human would recognise the call by, in preference order.
private val PREFERRED_ARG_KEYS = listOf(
    "path", "file_path", "command", "query", "question", "url",
    "name", "action", "agent_id", "prompt",
)

/**
 * The single most identifying argument, rendered as `(key: value)`. Literal
 * port of primaryArg in Message.tsx — used by the ToolFeed row's compact
 * one-line summary alongside the tool's raw name.
 */
fun ToolBadge.primaryArg(): String? {
    val obj = input as? JsonObject ?: return null
    val keys = obj.keys.toList()
    if (keys.isEmpty()) return null
    val key = PREFERRED_ARG_KEYS.firstOrNull { k ->
        val v = obj[k]
        v != null && v !is JsonNull && jsonValueString(v).isNotEmpty()
    } ?: keys.first()
    val raw = obj[key] ?: return null
    var value = jsonValueString(raw)
    if (key == "path" || key == "file_path") value = ToolStatus.shortPath(value)
    if (value.length > 44) value = value.take(44) + "…"
    return "($key: $value)"
}

/** A short "what came back", taken from the real result text. Port of resultSummary. */
fun ToolBadge.resultSummary(): String? {
    val r = result ?: return null
    val first = r.lineSequence().firstOrNull { it.isNotBlank() }?.trim() ?: return null
    if (first.isEmpty()) return null
    return if (first.length > 38) first.take(38) + "…" else first
}

enum class ToolStepState { Running, Failed, Done }

/** Done / running / failed — read off the result, never invented. Port of stepState. */
fun ToolBadge.stepState(live: Boolean): ToolStepState {
    if (live && result == null) return ToolStepState.Running
    val r = (result ?: "").lowercase()
    if (Regex("^(error|traceback|exception)\\b").containsMatchIn(r) || r.contains("error:")) {
        return ToolStepState.Failed
    }
    return ToolStepState.Done
}
