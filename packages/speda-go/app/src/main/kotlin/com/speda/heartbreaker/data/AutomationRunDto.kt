// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Wire types for an automation's run history — what
 * app/models/automation_run.py persists on every firing. Same "definition
 * vs. runs" split as ReminderDto.kt: AutomationInfo is the definition, this
 * is the ledger of what actually happened when it fired.
 */

@Serializable
data class AutomationRunInfo(
    val id: Int = 0,
    val status: String = "",
    val delivered: Boolean = false,
    val channel: String = "",
    val report: String = "",
    @SerialName("request_id") val requestId: String = "",
    @SerialName("fired_at") val firedAt: String = "",
)

@Serializable
data class AutomationRunHistoryResponse(val runs: List<AutomationRunInfo> = emptyList())
