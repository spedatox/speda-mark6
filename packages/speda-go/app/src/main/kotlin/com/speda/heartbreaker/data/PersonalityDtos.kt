// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later
package com.speda.heartbreaker.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class PersonalitySettings(
    val instructions: String = "",
    val tone: String = "default",
    val humor: String = "default",
    val directness: String = "default",
    @SerialName("response_length") val responseLength: String = "default",
)

@Serializable
data class AgentPersonalityInfo(
    @SerialName("agent_id") val agentId: String,
    val name: String,
    val domain: String,
    val settings: PersonalitySettings,
)
