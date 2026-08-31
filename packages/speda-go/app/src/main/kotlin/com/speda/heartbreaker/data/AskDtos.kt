// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * An irreversible operation an external peer's safety gate has stopped, waiting
 * on the owner (GET /agents/asks, and the `permission_request` SSE frame).
 *
 * [actionKey] is the exact command or path and is shown VERBATIM, never
 * truncated and never summarised: approving a force-push means knowing which
 * branch, and a card that elides the argument is a card that cannot be answered
 * honestly.
 *
 * [secondsLeft] is a deadline on the PEER's side, not a display flourish. When
 * it runs out the peer denies locally and carries on, so an ask the owner never
 * answers is a "no" — the card has to look like one rather than sit there
 * implying the decision is still open.
 *
 * Only the permission half of the desktop's InteractionPrompt is ported. Its
 * QuestionPrompt sibling is exported there and imported by nothing; porting a
 * surface no path can reach would be inventing a feature, not closing a gap.
 */
@Serializable
data class PendingAsk(
    @SerialName("ask_id") val askId: String,
    @SerialName("agent_id") val agentId: String = "",
    val tool: String = "",
    @SerialName("action_key") val actionKey: String = "",
    val reason: String = "",
    @SerialName("job_id") val jobId: String = "",
    /** null when the peer raised the ask outside a chat — a dispatched or
     *  background job. Those never reach the SSE path, which is exactly why the
     *  polled tray exists alongside it. */
    @SerialName("chat_id") val chatId: String? = null,
    @SerialName("seconds_left") val secondsLeft: Double = 0.0,
)
