package com.speda.heartbreaker.domain

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Tool → natural-language status and one-line summaries. Literal port of the
 * TOOL_STATUS map, statusLabel, toolSummary, isSearchTool and shortPath in
 * Message.tsx.
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

    private val SEARCH_TOOL_PATTERNS = listOf("tavily", "exa", "brave", "search", "fetch", "web")

    fun isSearchTool(name: String): Boolean {
        val n = name.lowercase()
        return SEARCH_TOOL_PATTERNS.any { n.contains(it) }
    }

    fun statusLabel(toolName: String): String =
        TOOL_STATUS[toolName] ?: "Using ${toolName.replace('_', ' ')}"

    /** Keep the last two path segments so long paths stay readable. */
    fun shortPath(p: String): String {
        val parts = p.split('/', '\\').filter { it.isNotEmpty() }
        return if (parts.size <= 2) p else "…/" + parts.takeLast(2).joinToString("/")
    }

    data class Summary(val verb: String, val target: String? = null)

    fun toolSummary(tool: ToolBadge): Summary {
        val path = tool.str("path")
        return when (tool.name) {
            "edit_file" -> Summary("Edited", path?.let(::shortPath))
            "write_file" -> Summary("Wrote", path?.let(::shortPath))
            "read_file" -> Summary("Read", path?.let(::shortPath))
            "run_command" -> Summary("Ran", tool.str("command"))
            "system_ops" -> when (tool.str("action")) {
                "read_file" -> Summary("Read", path?.let(::shortPath))
                "write_file" -> Summary("Wrote", path?.let(::shortPath))
                else -> Summary("Ran", tool.str("command"))
            }
            "graph_query" -> Summary("Searched graph", tool.str("question"))
            "graph_path" -> Summary("Traced the codebase graph")
            "graph_overview" -> Summary("Mapped the codebase graph")
            else -> if (isSearchTool(tool.name)) {
                Summary("Searched", tool.str("query") ?: tool.str("question"))
            } else {
                Summary(tool.name.replace('_', ' ').replace('-', ' '))
            }
        }
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
