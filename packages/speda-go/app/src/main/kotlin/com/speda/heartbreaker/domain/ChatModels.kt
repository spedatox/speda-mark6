// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

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

/** One step a delegated agent took, for the subagent panel (mirror of
 *  SubagentStep in lib/types.ts). */
@Immutable
data class SubagentStep(
    val kind: String,           // "tool" | "text"
    val tool: String? = null,
    val input: JsonElement? = null,
    val result: String? = null,
    val text: String? = null,
    val toolCallId: String? = null,
)

/**
 * One delegation a coding peer (Optimus, Centurion) made during a turn.
 *
 * It lives BESIDE the message, never inside `content`: a subagent's work is
 * not the answer. Streaming its report as prose is what made a delegate's
 * write-up read as the parent's own reply, and forwarding its completion as
 * the turn's `done` closed the stream while the peer was still working. This
 * is the channel that lets the work be SHOWN — foldable, ignorable — without
 * ever being mistaken for the response (mirror of SubagentRun in lib/types.ts).
 */
@Immutable
data class SubagentRun(
    val id: String,
    val agent: String = "",     // explore | review | general
    val label: String = "",     // what the delegation is FOR, not which specialist ran it
    val prompt: String? = null,
    val running: Boolean = true,
    val ok: Boolean? = null,
    val report: String? = null,
    val steps: PersistentList<SubagentStep> = persistentListOf(),
    val source: String? = null,
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
    /** What a coding peer delegated during this turn — see [SubagentRun]. Kept
     *  apart from `content`/`tools` for the same reason: never the answer. */
    val subagents: PersistentList<SubagentRun>? = null,
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
    /** The project (workspace) this chat belongs to, or null for a loose chat.
     *  Fixed when the chat is created and never changed — its history was
     *  produced under one set of standing instructions. The sidebar badges
     *  these rows rather than hiding them. */
    val projectId: Int? = null,
    val projectName: String? = null,
)

/**
 * A project — a named workspace owning its own chats, standing instructions and
 * knowledge base.
 *
 * Isolated per agent, exactly like chat history: the backend filters every
 * listing by agent_id and refuses a cross-agent read, so switching agents
 * switches the entire project set. There is no shared project.
 */
@Immutable
data class Project(
    val id: Int,
    val agentId: String,
    val name: String,
    /** The blurb on the card. Descriptive only — it is NOT sent to the model. */
    val description: String = "",
    /** Standing orders prepended to every turn in every chat in this project.
     *  This is the field that changes behaviour. */
    val instructions: String = "",
    val icon: String = "",
    val color: String = "",
    val pinned: Boolean = false,
    val archived: Boolean = false,
    val lastActivityAt: String = "",
    val chatCount: Int = 0,
    val fileCount: Int = 0,
)

/** One document in a project's knowledge base. The backend stores the EXTRACTED
 *  TEXT, not the original bytes, so [chars] (what the model sees) and [size]
 *  (what was uploaded) are different numbers on purpose. */
@Immutable
data class ProjectFile(
    val id: Int,
    val name: String,
    val mediaType: String = "",
    val size: Long = 0,
    val chars: Int = 0,
    val createdAt: String = "",
)
