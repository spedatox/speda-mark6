// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.domain

import com.speda.heartbreaker.i18n.AppStrings
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Tool → natural-language status and one-line summaries. Literal port of the
 * TOOL_STATUS map, statusLabel and shortPath in Message.tsx. Plain Kotlin, not
 * @Composable, so the label text itself lives in the i18n dict
 * (`AppStrings.toolStatus`) and is handed in by the caller.
 */
object ToolStatus {

    private val MCP_HASH_SUFFIX = Regex("_[0-9a-fA-F]{10}$")
    private val WORD_SEP = Regex("[_\\-\\s]+")

    /**
     * Turn an internal tool identifier into a human-readable lowercase label.
     * Literal port of `formatToolName` in Heartbreaker (`lib/toolName.ts`):
     * strips `mcp_<server>_<slug>_<10-hex-hash>` (and `mcp__<server>__<tool>`)
     * framing and collapses adjacent duplicate words (`google tasks tasks list`
     * -> `google tasks list`).
     */
    fun formatToolName(raw: String): String {
        val trimmed = raw.trim()
        if (trimmed.isEmpty()) return ""
        val isMcp = trimmed.startsWith("mcp_")
        var body = when {
            trimmed.startsWith("mcp__") -> trimmed.drop(5)
            isMcp -> trimmed.drop(4)
            else -> trimmed
        }
        if (isMcp) {
            body = body.replace(MCP_HASH_SUFFIX, "")
        }
        val words = body.split(WORD_SEP).filter { it.isNotEmpty() }
        val deduped = ArrayList<String>(words.size)
        for (w in words) {
            if (deduped.isNotEmpty() && deduped.last().equals(w, ignoreCase = true)) continue
            deduped.add(w)
        }
        return deduped.joinToString(" ")
    }

    fun displayName(raw: String): String =
        formatToolName(raw).replaceFirstChar { it.uppercase() }

    fun statusLabel(toolName: String, t: AppStrings): String =
        t.toolStatus.labels[toolName] ?: t.toolStatus.usingTool(formatToolName(toolName))

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

enum class ToolStepState { Running, Unconfirmed, Failed, Done }

/** Port of stepState. A tool without a receipt has no confirmed outcome. */
fun ToolBadge.stepState(live: Boolean): ToolStepState {
    val r = result?.trimStart()?.lowercase()
        ?: return if (live) ToolStepState.Running else ToolStepState.Unconfirmed
    if (Regex("^(error|traceback|exception)\\b").containsMatchIn(r) || r.contains("error:")) {
        return ToolStepState.Failed
    }
    return ToolStepState.Done
}
