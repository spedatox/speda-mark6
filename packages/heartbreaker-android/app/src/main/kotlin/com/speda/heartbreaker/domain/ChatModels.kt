package com.speda.heartbreaker.domain

import androidx.compose.runtime.Immutable
import kotlinx.collections.immutable.PersistentList
import kotlinx.collections.immutable.persistentListOf
import kotlinx.serialization.json.JsonElement

/**
 * Chat domain models — the Kotlin mirror of lib/types.ts. Lists are
 * [PersistentList] so the reducer stays value-comparable and Compose can skip
 * unchanged rows (the MemoMessage discipline maps onto Compose stability).
 */

enum class Role { User, Assistant }

@Immutable
data class ToolBadge(
    val id: String,
    val name: String,
    /** Arbitrary tool arguments (JSON) — what the model searched/added/ran. */
    val input: JsonElement? = null,
    /** Truncated tool output. */
    val result: String? = null,
    /**
     * How many chars of `content` had streamed when this tool fired — lets the
     * renderer interleave it where it actually happened. Null (older stored
     * messages) is treated as 0, reproducing the stacked-on-top behaviour.
     */
    val afterChars: Int? = null,
)

@Immutable
data class FileMeta(
    val name: String,
    val title: String,
    val kind: String,
    val size: Long,
    val url: String,
)

@Immutable
data class UploadedFile(val name: String, val size: Long)

@Immutable
data class ChatMessage(
    val id: String,
    val role: Role,
    val content: String,
    val tools: PersistentList<ToolBadge> = persistentListOf(),
    val isStreaming: Boolean = false,
    val isError: Boolean = false,
    /** Error banner text — kept SEPARATE from content so a mid-turn failure never
     *  erases what already streamed. */
    val errorNote: String? = null,
    val images: PersistentList<String>? = null,
    val files: PersistentList<FileMeta>? = null,
    val uploads: PersistentList<UploadedFile>? = null,
    /** Live status line while streaming (real phase, not looped filler). */
    val status: String? = null,
    /** Which session a STREAMING bubble belongs to — lets SELECT_SESSION preserve
     *  an in-flight tail instead of wiping it in the history-load race. */
    val sessionId: Int? = null,
)

@Immutable
data class Session(
    val id: Int,
    val title: String?,
    val startedAt: String,
)
